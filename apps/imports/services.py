"""Обход папок, приём и отклонение описей. Одна реализация на всё: команда,
расписание и кнопка зовут эти функции (граф APCS: debt-import-two-implementations).

Обход (ТЗ 1.5–1.8, 2):
  1. путь, размер и время изменения совпали — книга не открывается;
  2. иначе SHA-256; такой хеш уже есть у описи — файл перемещён или
     скопирован, разбора нет (отклонённая с тем же хешом не предлагается);
  3. иначе книга разбирается (параллельно в процессах) и встаёт в просмотр;
     строки как в актуальной версии узла — «без изменений», в просмотр не идёт.
"""

import datetime as dt
import hashlib
import logging
import os
from concurrent.futures import ProcessPoolExecutor, as_completed

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.inventories.models import Inventory, Item, ObjectType, Reconciliation, Rule, SourceFile, Status
from apps.orgs.folders import parse_path
from apps.orgs.models import FolderMapping, Node
from apps.stats import services as stats

from .models import ScanRun
from .reader import parse_book

log = logging.getLogger(__name__)

SCAN_LOCK = 7_301_001  # ключ блокировки обхода в PostgreSQL (граф APCS: debt-lock-release-not-atomic)
ITEM_FIELDS = [f.name for f in Item._meta.fields
               if f.name not in ("id", "inventory", "status", "object_type")]


# ------------------------------------------------------------------ обход

def _walk(root, exts):
    for cur, dirs, files in os.walk(root, onerror=lambda e: log.warning("нет доступа: %s", e)):
        dirs.sort(key=str.lower)
        for name in sorted(files, key=str.lower):
            if name.startswith("~$"):
                continue  # временный файл открытой книги
            if os.path.splitext(name)[1].lower() in exts:
                yield os.path.join(cur, name)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(roots=None, workers=None) -> ScanRun:
    """Обойти папки. Один обход за раз — блокировка в базе, снимается сама
    при обрыве соединения."""
    roots = [os.path.normpath(r) for r in (roots or settings.INVENTORY_ROOTS)]
    run = ScanRun.objects.create(roots=roots)
    with connection.cursor() as c:
        c.execute("select pg_try_advisory_lock(%s)", [SCAN_LOCK])
        got = c.fetchone()[0]
    if not got:
        run.state, run.finished_at = ScanRun.State.BUSY, timezone.now()
        run.save()
        return run
    notes = []
    try:
        exts = {e.lower() for e in settings.INVENTORY_EXTENSIONS}
        known = {sf.path: sf for sf in SourceFile.objects.filter(root__in=roots)}
        seen, todo, todo_sha = set(), [], set()
        now = timezone.now()
        for root in roots:
            if not os.path.isdir(root):
                notes.append(f"папка недоступна: {root}")
                run.errors += 1
                continue
            for path in _walk(root, exts):
                run.found += 1
                seen.add(path)
                try:
                    st = os.stat(path)
                except OSError as e:
                    notes.append(f"нет доступа: {path}: {e}")
                    run.errors += 1
                    continue
                mtime = dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc)
                sf = known.get(path)
                if sf and sf.size == st.st_size and abs((sf.mtime - mtime).total_seconds()) < 1:
                    run.unchanged += 1
                    if sf.missing:
                        sf.missing = False
                    sf.last_seen = now
                    sf.save(update_fields=["last_seen", "missing"])
                    continue
                try:
                    sha = _sha256(path)
                except OSError as e:
                    notes.append(f"не читается: {path}: {e}")
                    run.errors += 1
                    continue
                rel = os.path.relpath(os.path.dirname(path), root)
                sf, _ = SourceFile.objects.update_or_create(path=path, defaults=dict(
                    root=root, rel_folder="" if rel == "." else rel, name=os.path.basename(path),
                    size=st.st_size, mtime=mtime, sha256=sha, last_seen=now, missing=False))
                # То же содержимое уже есть (в базе или в этом же обходе) — без разбора.
                if sha in todo_sha or Inventory.objects.filter(sha256=sha).exclude(
                        state=Inventory.State.ERROR).exists():
                    run.moved += 1
                    continue
                todo.append(sf)
                todo_sha.add(sha)
        gone = [sf for p, sf in known.items() if p not in seen and not sf.missing]
        for sf in gone:
            sf.missing = True
            sf.save(update_fields=["missing"])
        run.missing = len(gone)

        for sf, parsed in _parse_all(todo, workers):
            run.parsed += 1
            try:
                inv = _stage(sf, parsed)
            except Exception as e:  # одна книга не валит обход, но и не теряется
                log.exception("опись не записана: %s", sf.path)
                notes.append(f"не записана: {sf.path}: {type(e).__name__}: {e}")
                run.errors += 1
                continue
            if inv.state == Inventory.State.ERROR:
                run.errors += 1
                notes.append(f"{sf.path}: {inv.error}")
            elif inv.state == Inventory.State.UNCHANGED:
                run.same_content += 1
            else:
                run.pending += 1
        run.state = ScanRun.State.DONE_ERRORS if run.errors else ScanRun.State.DONE
    except Exception as e:
        log.exception("обход прерван")
        run.state = ScanRun.State.FAILED
        notes.append(f"обход прерван: {type(e).__name__}: {e}")
    finally:
        with connection.cursor() as c:
            c.execute("select pg_advisory_unlock(%s)", [SCAN_LOCK])
        run.finished_at = timezone.now()
        run.log = "\n".join(notes)
        run.save()
    return run


def _parse_all(files, workers=None):
    """Разбор книг в процессах (на Windows — spawn); по одной — без пула."""
    if not files:
        return
    workers = workers or max(1, min((os.cpu_count() or 2) - 1, 8, len(files)))
    if len(files) < 3 or workers == 1:
        for sf in files:
            yield sf, parse_book(sf.path)
        return
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(parse_book, sf.path): sf for sf in files}
        for fu in as_completed(futs):
            sf = futs[fu]
            try:
                yield sf, fu.result()
            except Exception as e:
                yield sf, {"error": f"процесс разбора упал: {type(e).__name__}: {e}", "sha256": sf.sha256}


# ------------------------------------------------------------------ запись

def _resolve_node(rel_folder):
    """Узел по пути папки: (узел или None, предложенный путь, определён ли)."""
    mapped = FolderMapping.objects.filter(folder=rel_folder).select_related("node").first()
    if mapped:
        return mapped.node, [], True
    parts = [p for p in rel_folder.replace("/", "\\").split("\\") if p]
    if not parts:
        return None, [], False
    proposed, ok = parse_path(parts)
    if not ok:
        return None, proposed, False
    path = "/" + "/".join(p["code"] for p in proposed) + "/"
    return Node.objects.filter(path=path).first(), proposed, True


def _stage(sf, p) -> Inventory:
    """Записать разобранную книгу описью «ждёт просмотра» (одна транзакция)."""
    node, proposed, resolved = _resolve_node(sf.rel_folder)
    base = dict(source_file=sf, sha256=p.get("sha256") or sf.sha256, node=node,
                proposed_path=proposed, node_resolved=resolved)
    if p.get("error"):
        return Inventory.objects.create(state=Inventory.State.ERROR, error=p["error"], **base)
    current = Inventory.objects.filter(node=node, is_current=True).first() if node else None
    if current and current.content_hash == p["content_hash"]:
        return Inventory.objects.create(state=Inventory.State.UNCHANGED, content_hash=p["content_hash"],
                                        rows=len(p["items"]), **base)
    statuses = {s.name.lower(): s for s in Status.objects.all()}
    types = {t.name: t for t in ObjectType.objects.all()}
    with transaction.atomic():
        with connection.cursor() as c:  # импорт больше обычного запроса — свой предел времени
            c.execute("SET LOCAL statement_timeout = '600s'")
        inv = Inventory.objects.create(
            state=Inventory.State.PENDING, content_hash=p["content_hash"],
            org_text=p["org_text"][:1000], source_date=p["source_date"], source_name=p["source_name"][:1000],
            source_sheet=p["source_sheet"][:300], source_kind=p["source_kind"][:300],
            source_hierarchy=p["source_hierarchy"][:300], source_folder=p["source_folder"][:1000],
            cost_basis=p["cost_basis"][:300], check_flag=sf.name.lstrip().startswith("!"),
            summary_lines=p["summary_lines"], unknown_columns=p["unknown_columns"], **base)
        items = []
        for it in p["items"]:
            st_name = it["status_doc"]
            status = statuses.get(st_name.lower())
            if status is None and st_name:
                status = statuses[st_name.lower()] = Status.objects.create(name=st_name)
            ot = None
            if it["object_type"]:
                ot = types.get(it["object_type"])
                if ot is None:
                    ot = types[it["object_type"]] = ObjectType.objects.create(name=it["object_type"][:300])
            items.append(Item(inventory=inv, status=status, object_type=ot,
                              **{f: it.get(f) for f in ITEM_FIELDS if f in it}))
        Item.objects.bulk_create(items, batch_size=2000)
        Reconciliation.objects.bulk_create([Reconciliation(inventory=inv, **r) for r in p["reconciliations"]])
        Rule.objects.bulk_create([Rule(inventory=inv, **r) for r in p["rules"]])
        agg = Item.objects.filter(inventory=inv).aggregate(
            q=Sum("qty"), ci=Sum("cost_initial"), cb=Sum("cost_balance"))
        inv.rows, inv.qty = len(items), agg["q"] or 0
        inv.cost_initial, inv.cost_balance = agg["ci"] or 0, agg["cb"] or 0
        inv.save(update_fields=["rows", "qty", "cost_initial", "cost_balance"])
    return inv


# ------------------------------------------------------------------ решение

def _ensure_node(inv, user):
    """Узел для приёма: найденный, созданный по пути папки или ошибка."""
    if inv.node_id:
        return inv.node
    if not inv.node_resolved or not inv.proposed_path:
        raise ValueError("узел не определён — укажите узел")
    parent = None
    for part in inv.proposed_path:
        node = Node.objects.filter(parent=parent, code=part["code"]).first()
        if node is None:
            node = Node(parent=parent, code=part["code"], name=part["name"], created_by=user)
            node.save()
        elif not node.name and part["name"]:
            node.name = part["name"]
            node.save(update_fields=["name"])
        parent = node
    return parent


@transaction.atomic
def accept(inv: Inventory, user=None, node: Node = None) -> Inventory:
    """Принять опись: она — актуальная версия узла; прежняя уходит в историю."""
    inv = Inventory.objects.select_for_update().get(pk=inv.pk)
    if inv.state != Inventory.State.PENDING:
        raise ValueError(f"опись не ждёт просмотра (состояние: {inv.get_state_display()})")
    if node is not None:
        inv.node = node
        if inv.source_file.rel_folder and not inv.node_resolved:
            FolderMapping.objects.update_or_create(folder=inv.source_file.rel_folder, defaults={"node": node})
    else:
        inv.node = _ensure_node(inv, user)
    now = timezone.now()
    prev = Inventory.objects.select_for_update().filter(node=inv.node, is_current=True).first()
    if prev:
        prev.is_current, prev.valid_to = False, now
        prev.save(update_fields=["is_current", "valid_to"])
        stats.close_stats(prev, now)
    inv.state, inv.is_current, inv.valid_from = Inventory.State.ACCEPTED, True, now
    inv.decided_at, inv.decided_by, inv.node_resolved = now, user, True
    inv.save()
    stats.open_stats(inv, now)
    return inv


@transaction.atomic
def reject(inv: Inventory, user=None) -> Inventory:
    """Отклонить: опись остаётся записью (хеш — чтобы не предлагать снова), позиции удаляются."""
    inv = Inventory.objects.select_for_update().get(pk=inv.pk)
    if inv.state != Inventory.State.PENDING:
        raise ValueError(f"опись не ждёт просмотра (состояние: {inv.get_state_display()})")
    inv.items.all().delete()
    inv.state, inv.decided_at, inv.decided_by = Inventory.State.REJECTED, timezone.now(), user
    inv.save(update_fields=["state", "decided_at", "decided_by"])
    return inv
