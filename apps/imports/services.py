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
import re
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from django.conf import settings
from django.db import connection, reset_queries, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.inventories.models import Inventory, Item, ObjectType, Reconciliation, Rule, SourceFile, Status
from apps.orgs.folders import parse_path
from apps.orgs.models import FolderMapping, Node
from apps.orgs.services import ensure_path
from apps.stats import services as stats
from apps.stats.models import VersionStat

from .models import ScanFile, ScanRun
from .reader import parse_book

log = logging.getLogger(__name__)

SCAN_LOCK = 7_301_001  # ключ блокировки обхода в PostgreSQL (граф APCS: debt-lock-release-not-atomic)
ITEM_FIELDS = [f.name for f in Item._meta.fields
               if f.name not in ("id", "inventory", "status", "object_type")]


# ------------------------------------------------------------------ обход

def _in_processed(path):
    """Книга лежит в папке результата нормализации («обработанные») — где угодно в пути."""
    wanted = {w.strip().lower() for w in settings.PROCESSED_FOLDERS}
    if not wanted:
        return True
    return any(part.strip().lower() in wanted for part in os.path.normpath(path).split(os.sep))


def _walk(root, exts, processed_only=True, failed=None):
    """(путь, читать ли): вне «обработанных» книга не открывается — только имя в дерево обхода.
    failed — сюда пишутся папки, которые не прочитались (их книги не считаются пропавшими)."""
    def onerror(e):
        log.warning("нет доступа: %s", e)
        if failed is not None and getattr(e, "filename", None):
            failed.append(os.path.normpath(e.filename))

    for cur, dirs, files in os.walk(root, onerror=onerror):
        dirs.sort(key=str.lower)
        take = not processed_only or _in_processed(cur)
        for name in sorted(files, key=str.lower):
            if name.startswith("~$"):
                continue  # временный файл открытой книги
            if os.path.splitext(name)[1].lower() in exts:
                yield os.path.join(cur, name), take


class _Files:
    """Итоги по файлам обхода — в базу пачками (дерево файлов на странице обхода)."""

    def __init__(self, run):
        self.run, self.buf = run, []

    def add(self, root, path, status, note="", inventory_id=None):
        rel = os.path.relpath(os.path.dirname(path), root)
        self.buf.append(ScanFile(run=self.run, root=root, rel_dir="" if rel == "." else rel,
                                 name=os.path.basename(path), status=status, note=note[:4000],
                                 inventory_id=inventory_id))
        if len(self.buf) >= 500:
            self.flush()

    def flush(self):
        if self.buf:
            ScanFile.objects.bulk_create(self.buf)
            self.buf = []


PROGRESS = ["found", "ignored", "unchanged", "moved", "parsed", "pending", "accepted", "same_content", "errors",
            "warnings", "stage", "to_parse", "current", "mem_mb", "mem_peak_mb"]


class MemoryLimit(Exception):
    """Процесс обхода перешёл предел памяти — обход останавливается сам."""


def _rss_mb():
    try:
        import psutil
        return int(psutil.Process().memory_info().rss / 1048576)
    except Exception:  # psutil нет — без замера
        return 0


class Cancelled(Exception):
    """Обход остановлен кнопкой «Остановить обход» или «Очистить базу»."""


def _progress(run, every=20, force=False):
    """Ход обхода в базу — чтобы экран показывал, что идёт, не дожидаясь конца;
    заодно — не просили ли остановить."""
    n = run.found + run.parsed + run.ignored
    if force or n % every == 0:
        run.mem_mb = _rss_mb()
        run.mem_peak_mb = max(run.mem_peak_mb, run.mem_mb)
        run.save(update_fields=PROGRESS)
        if ScanRun.objects.filter(pk=run.pk, cancel=True).exists():
            raise Cancelled()
        if run.mem_mb and run.mem_mb > settings.SCAN_MEMORY_LIMIT_MB:
            raise MemoryLimit()


def scan(roots=None, workers=None, run=None, processed_only=True, mark_missing=True) -> ScanRun:
    """Обойти папки. Один обход за раз — блокировка в базе, снимается сама
    при обрыве соединения. Для загрузки с компьютера (scan_upload): книги уже
    отобраны в браузере — processed_only=False; в папке лишь эта загрузка, а не
    все книги — прежние не помечаются пропавшими (mark_missing=False)."""
    roots = [os.path.normpath(r) for r in (roots or settings.INVENTORY_ROOTS)]
    run = run or ScanRun.objects.create(roots=roots)
    run.roots, run.stage = roots, "поиск файлов"
    run.save(update_fields=["roots", "stage"])
    with connection.cursor() as c:
        c.execute("select pg_try_advisory_lock(%s)", [SCAN_LOCK])
        got = c.fetchone()[0]
    if not got:
        run.state, run.finished_at = ScanRun.State.BUSY, timezone.now()
        run.save()
        return run
    # Блокировка наша — значит, «идущие» обходы без конца оборваны (сервер остановили).
    ScanRun.objects.filter(finished_at__isnull=True).exclude(pk=run.pk).update(
        state=ScanRun.State.FAILED, finished_at=timezone.now(), stage="", current="",
        log="обход прерван: сервер остановлен до конца обхода")
    notes = []
    try:
        exts = {e.lower() for e in settings.INVENTORY_EXTENSIONS}
        known = {sf.path: sf for sf in SourceFile.objects.filter(root__in=roots)}
        seen, todo, seen_sha = set(), [], {}
        files = _Files(run)
        S = ScanFile.Status
        now = timezone.now()
        walked, failed = [], []  # корни, обойдённые целиком и с книгами; папки, что не прочитались
        for root in roots:
            if not os.path.isdir(root):
                notes.append(f"папка недоступна: {root}")
                run.errors += 1
                continue
            found_before = run.found
            for path, take in _walk(root, exts, processed_only, failed):
                if not take:
                    run.ignored += 1
                    files.add(root, path, S.IGNORED)
                    _progress(run)
                    continue
                run.found += 1
                run.current = path
                _progress(run)
                seen.add(path)
                try:
                    st = os.stat(path)
                except OSError as e:
                    notes.append(f"нет доступа: {path}: {e}")
                    run.errors += 1
                    files.add(root, path, S.ERROR, f"нет доступа: {e}")
                    continue
                mtime = dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc)
                sf = known.get(path)
                # Без изменений — если по файлу есть опись; книга, которая не записалась
                # (сбой записи), берётся заново и без изменения файла. Вернувшаяся в папку
                # (была снята с актуальных как пропавшая) — тоже заново: снова станет актуальной.
                if (sf and not sf.missing and sf.size == st.st_size and abs((sf.mtime - mtime).total_seconds()) < 1
                        and sf.inventories.exists()):
                    run.unchanged += 1
                    sf.last_seen = now
                    sf.save(update_fields=["last_seen"])
                    files.add(root, path, S.UNCHANGED)
                    continue
                # Новая или изменённая книга — в разбор; хеш считает разбор из тех же
                # байтов (одно чтение по сети вместо двух).
                rel = os.path.relpath(os.path.dirname(path), root)
                todo.append((path, root, "" if rel == "." else rel, st.st_size, mtime))
            if run.found > found_before:
                walked.append(root)
            else:  # ни одной книги — скорее сбой сети или не та папка, чем пустая папка
                notes.append(f"в папке нет ни одной книги — пропавшие с актуальных не снимаются: {root}")
        files.flush()
        if mark_missing:
            run.missing = _retire_gone(known, seen, walked, failed, now, notes)

        run.stage, run.to_parse = "разбор книг", len(todo)
        _progress(run, force=True)
        copies = 0
        for (path, root, rel, size, mtime), parsed in _parse_all(todo, workers):
            run.current = path
            # DEBUG копит текст каждого запроса (вставка 2 000 позиций — огромный) — чистим.
            reset_queries()
            sha = parsed.get("sha256") or ""
            sf, _ = SourceFile.objects.update_or_create(path=path, defaults=dict(
                root=root, rel_folder=rel, name=os.path.basename(path), size=size, mtime=mtime,
                sha256=sha, last_seen=now, missing=False))
            # Копия: то же содержимое уже есть — в базе или раньше в этом обходе.
            if sha:
                # Двойник — другой файл и на месте: перенесённая книга (оригинал пропал) и
                # вернувшаяся в папку (своя прежняя опись) — не копии.
                twin = seen_sha.get(sha) or Inventory.objects.filter(sha256=sha, source_file__missing=False).exclude(
                    state=Inventory.State.ERROR).exclude(source_file=sf).values_list("source_file__path", flat=True).first()
                if twin:
                    run.moved += 1
                    copies += 1
                    if copies <= 500:
                        notes.append(f"копия: {path} — как {twin}")
                    files.add(root, path, S.COPY, f"как {twin}")
                    run.parsed += 1
                    _progress(run, every=5)
                    continue
                seen_sha[sha] = path
            issues = data_issues(parsed)
            if issues:
                run.warnings += 1
                notes.append(f"{path}: {issues}")
            try:
                inv = _stage(sf, parsed)
            except Exception as e:  # одна книга не валит обход, но и не теряется
                log.exception("опись не записана: %s", sf.path)
                notes.append(f"не записана: {sf.path}: {type(e).__name__}: {e}")
                run.errors += 1
                files.add(root, path, S.ERROR, f"не записана: {type(e).__name__}: {e}")
                run.parsed += 1
                _progress(run, every=5)
                continue
            # База не для ручной работы (решение пользователя 16.10.2026): опись с узлом
            # принимается сразу, без просмотра.
            if inv.state == Inventory.State.PENDING and inv.node_id and settings.AUTO_ACCEPT:
                try:
                    inv = accept(inv)
                except Exception as e:
                    notes.append(f"не принята: {sf.path}: {e}")
            if inv.state == Inventory.State.ERROR:
                run.errors += 1
                notes.append(f"{sf.path}: {inv.error}")
                files.add(root, path, S.ERROR, inv.error, inv.pk)
            elif inv.state == Inventory.State.UNCHANGED:
                run.same_content += 1
                files.add(root, path, S.SAME, issues, inv.pk)
            elif inv.state == Inventory.State.PENDING:
                run.pending += 1
                files.add(root, path, S.PENDING, issues, inv.pk)
            else:
                run.accepted += 1
                files.add(root, path, S.ACCEPTED, issues, inv.pk)
            run.parsed += 1
            _progress(run, every=5)
            del parsed, inv
        files.flush()
        run.state = ScanRun.State.DONE_ERRORS if run.errors else ScanRun.State.DONE
    except Cancelled:
        files.flush()
        run.state = ScanRun.State.CANCELLED
        notes.append("обход остановлен пользователем")
    except MemoryLimit:
        files.flush()
        run.state = ScanRun.State.CANCELLED
        notes.append(f"обход остановлен: память процесса {run.mem_mb} МБ — больше предела "
                     f"{settings.SCAN_MEMORY_LIMIT_MB} МБ (SCAN_MEMORY_LIMIT_MB)")
    except Exception as e:
        log.exception("обход прерван")
        run.state = ScanRun.State.FAILED
        notes.append(f"обход прерван: {type(e).__name__}: {e}")
    finally:
        with connection.cursor() as c:
            c.execute("select pg_advisory_unlock(%s)", [SCAN_LOCK])
        run.finished_at = timezone.now()
        run.stage, run.current = "", ""
        run.log = "\n".join(notes)
        run.log_file = write_log(run, notes)
        run.save()
    return run


def _retire_gone(known, seen, walked, failed, now, notes):
    """Книги, пропавшие из обойдённых папок: «нет в папке», их опись — не актуальна.
    Модуль нормализации при пересборке удаляет прежний файл и часто меняет имя
    («!ПРОВЕРИТЬ — », разбиение по листам) — без этого старая опись оставалась
    актуальной рядом с новой и свод считал её дважды (21.10.2026). Не трогаются
    книги корней без единой найденной книги и папок, что не прочитались."""
    failed = [os.path.normcase(d.rstrip("\\/")) + os.sep for d in failed]
    gone = [sf for p, sf in known.items()
            if p not in seen and not sf.missing and sf.root in walked
            and not any(os.path.normcase(p).startswith(d) for d in failed)]
    for sf in gone:
        sf.missing = True
        sf.save(update_fields=["missing"])
        for inv in Inventory.objects.filter(source_file=sf, is_current=True):
            retire(inv, now)
            notes.append(f"снята с актуальных — файла нет в папке: {sf.path}")
    return len(gone)


@transaction.atomic
def retire(inv: Inventory, at=None) -> Inventory:
    """Опись больше не актуальна (файл пропал из папки): срок действия закрыт,
    свод версии — тоже; «на дату» до этого момента она видна как прежде."""
    at = at or timezone.now()
    inv.is_current, inv.valid_to = False, at
    inv.save(update_fields=["is_current", "valid_to"])
    stats.close_stats(inv, at)
    return inv


def scan_busy():
    """Обход идёт (держит блокировку) или ждёт в очереди обработчика. Запись без
    конца после остановки сервера — не обход: её закроет следующий обход."""
    with connection.cursor() as c:
        c.execute("select exists(select 1 from pg_locks where locktype = 'advisory' and granted"
                  " and classid = 0 and objid = %s and objsubid = 1)", [SCAN_LOCK])
        if c.fetchone()[0]:
            return True
    return ScanRun.objects.filter(finished_at__isnull=True, stage="в очереди",
                                  started_at__gte=timezone.now() - dt.timedelta(hours=1)).exists()


def upload_root():
    return os.path.normpath(str(settings.UPLOAD_DIR))


def clear_upload():
    """Стереть загруженные книги: в базе остаются разобранные сведения, путь и хеш."""
    import shutil
    root = upload_root()
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)


def scan_upload(run=None, workers=None) -> ScanRun:
    """Разобрать книги, перетащенные в «Импорт», и стереть их (решение пользователя
    19.10.2026 «Разобрать и стереть»). Обход тот же, что для папки."""
    root = upload_root()
    try:
        run = scan([root], workers=workers, run=run, processed_only=False, mark_missing=False)
        twins = _twins_elsewhere(root, run)
        if twins:
            notes = [x for x in (run.log or "").split("\n") if x] + twins
            run.log = "\n".join(notes)
            run.log_file = write_log(run, notes) or run.log_file
            run.save(update_fields=["log", "log_file"])
        return run
    finally:
        clear_upload()


def _twins_elsewhere(root, run):
    """Загруженная книга с тем же путём (папка от корня и имя), что у книги из
    папки на сервере, — другая книга для учёта версий: актуальными стали обе."""
    out = []
    loaded = SourceFile.objects.filter(root=root, last_seen__gte=run.started_at).exclude(
        inventories__state__in=[Inventory.State.ERROR, Inventory.State.REJECTED])
    for sf in loaded.distinct():
        other = (SourceFile.objects.filter(rel_folder=sf.rel_folder, name=sf.name, inventories__is_current=True)
                 .exclude(root=root).values_list("path", flat=True).first())
        if other:
            out.append(f"та же книга есть из папки: {sf.rel_folder}\\{sf.name} — уже загружена как {other}; "
                       f"актуальны обе, проверьте свод")
    return out


UNIT_NAME =re.compile(r"^\s*(т|кг|г|л|м|м2|м3|кв\.?\s*м\.?|шт\.?|п\.?\s*м\.?)\s*$", re.I)


def data_issues(parsed):
    """Ошибки данных книги одной строкой (решение пользователя 16.10.2026: «При
    импорте если ошибки встречаются, выбрасываем в лог ошибку по файлам»)."""
    items = parsed.get("items") or []
    money = [i for i in items if (i.get("qty_note") or "").startswith("в количестве")]
    swapped = [i for i in money if "заменена" in i["qty_note"]]
    unit = [i for i in items if UNIT_NAME.match(i.get("name") or "")]
    neg = [i for i in items if (i.get("qty") or "").startswith("-")]
    measures = [i for i in items if i.get("excluded")]
    out = []
    if money:
        out.append(f"сумма в «Количестве» — {len(money)} стр. (из них переставлены количество и стоимость — "
                   f"{len(swapped)}), посчитаны за 1 шт.; строки: {_rows(money)}")
    if unit:
        out.append(f"вместо названия — единица измерения — {len(unit)} стр.: {_rows(unit)}")
    if measures:
        out.append(f"исключены из свода (меры: материалы, запасы) — {len(measures)} стр.: {_rows(measures)}")
    if neg:
        out.append(f"отрицательное количество — {len(neg)} стр.: {_rows(neg)}")
    return "; ".join(out)


def _rows(items, limit=20):
    s = ", ".join(f"{i['sheet']} {i['row_no']}" for i in items[:limit])
    return s + (f" и ещё {len(items) - limit}" if len(items) > limit else "")


def write_log(run, notes):
    """Журнал обхода файлом: logs/import/обход-<номер>-<дата>.log — по строке на файл."""
    folder = str(settings.IMPORT_LOG_DIR)
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"обход-{run.pk}-{timezone.localtime(run.started_at):%Y-%m-%d_%H%M}.log")
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(f"Обход {run.pk}: {', '.join(run.roots or [])}\n")
            f.write(f"Начало {timezone.localtime(run.started_at):%d.%m.%Y %H:%M}, конец "
                    f"{timezone.localtime(run.finished_at):%d.%m.%Y %H:%M}; итог: {run.get_state_display()}\n")
            f.write(f"Файлов {run.found}, разобрано {run.parsed}, принято {run.accepted}, копий {run.moved}, "
                    f"ошибок {run.errors}, с ошибками данных {run.warnings}\n\n")
            f.write("\n".join(notes) + "\n")
        return path
    except OSError as e:
        log.warning("журнал обхода не записан: %s", e)
        return ""


def _parse_all(files, workers=None):
    """Разбор книг в процессах (на Windows — spawn); по одной — без пула."""
    if not files:
        return
    # Процессов немного (настройка INVENTORY_WORKERS, по умолчанию 2): память
    # важнее скорости — сервер слабый, рядом работают люди. Процесс через 20
    # книг сменяется новым, чтобы память не копилась.
    workers = max(1, min(workers or settings.INVENTORY_WORKERS, len(files)))
    if len(files) < 3 or workers == 1:
        for f in files:
            yield f, parse_book(f[0])
        return
    # В работе — не больше двух книг на процесс: готовая отпускается сразу. Прежде
    # as_completed держал все готовые задачи с результатами до конца обхода — утечка
    # (16.10.2026: 1,4 ГБ на 1 300 книгах).
    ex = ProcessPoolExecutor(max_workers=workers, max_tasks_per_child=20)
    queue, inflight = list(files), {}
    try:
        while queue or inflight:
            while queue and len(inflight) < workers * 2:
                f = queue.pop(0)
                inflight[ex.submit(parse_book, f[0])] = f
            done, _ = wait(list(inflight), return_when=FIRST_COMPLETED)
            for fu in done:
                f = inflight.pop(fu)
                try:
                    res = fu.result()
                except Exception as e:
                    res = {"error": f"процесс разбора упал: {type(e).__name__}: {e}", "sha256": ""}
                fu._result = None  # не держать строки книги в объекте задачи
                yield f, res
                del res
    finally:  # остановили — книги из очереди не читаются, идущие дочитываются
        ex.shutdown(wait=True, cancel_futures=True)


# ------------------------------------------------------------------ запись

def _resolve_node(rel_folder):
    """Узел по пути папки: (узел или None, предложенный путь, определён ли)."""
    mapped = FolderMapping.objects.filter(folder=rel_folder).select_related("node").first()
    if mapped:
        return mapped.node, [], True
    parts = [p for p in rel_folder.replace("/", "\\").split("\\") if p]
    proposed = parse_path(parts)
    if not proposed:
        return None, [], False  # книга в корне выбранной папки — узел укажет пользователь
    # Узлы создаются сразу при обходе: дерево видно до приёма описей.
    return ensure_path(proposed), proposed, True


def _stage(sf, p) -> Inventory:
    """Записать разобранную книгу описью «ждёт просмотра» (одна транзакция)."""
    node, proposed, resolved = _resolve_node(sf.rel_folder)
    base = dict(source_file=sf, sha256=p.get("sha256") or sf.sha256, node=node,
                proposed_path=proposed, node_resolved=resolved)
    if p.get("error"):
        return Inventory.objects.create(state=Inventory.State.ERROR, error=p["error"], **base)
    # Версии — по файлу: у узла бывает несколько описей сразу (бюджет, спецсчёт).
    current = Inventory.objects.filter(source_file=sf, is_current=True).first()
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
        agg = Item.objects.filter(inventory=inv, excluded=False).aggregate(
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
    return ensure_path(inv.proposed_path, user)


@transaction.atomic
def accept(inv: Inventory, user=None, node: Node = None, at=None) -> Inventory:
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
    now = at or timezone.now()  # at — только для тестового набора с историей
    # Прежняя версия — та же книга (тот же путь); другие описи узла остаются актуальными.
    prev = Inventory.objects.select_for_update().filter(source_file=inv.source_file, is_current=True).first()
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


# ------------------------------------------------------------------ массовый приём

def accept_many(ids, user=None, job=None):
    """Принять описи по одной (каждая — своя транзакция: сбой одной не откатывает
    остальные); ход — в AcceptJob. Узел должен быть определён."""
    from .models import AcceptJob
    job = job or AcceptJob.objects.create(by=user, total=len(ids))
    notes = []
    for n, pk in enumerate(ids, 1):
        try:
            accept(Inventory.objects.get(pk=pk), user=user)
            job.done += 1
        except Exception as e:  # отказ одной — в журнал, а не тишина
            job.failed += 1
            if len(notes) < 300:
                notes.append(f"{pk}: {e}")
        if n % 25 == 0:
            job.save(update_fields=["done", "failed"])
    job.finished_at, job.log = timezone.now(), "\n".join(notes)
    job.save()
    return job


def reassign_nodes():
    """Разложить по узлам описи, у которых узла нет: узел — по пути папки книги
    (без повторного чтения файлов). Возвращает (разложено, осталось без узла)."""
    done = left = 0
    qs = Inventory.objects.filter(node__isnull=True).exclude(state=Inventory.State.REJECTED).select_related("source_file")
    for inv in qs.iterator(chunk_size=500):
        node, proposed, resolved = _resolve_node(inv.source_file.rel_folder)
        if node is None:
            left += 1
            continue
        Inventory.objects.filter(pk=inv.pk).update(node=node, node_resolved=True, proposed_path=proposed)
        VersionStat.objects.filter(inventory_id=inv.pk).update(node=node)
        done += 1
    return done, left


# ------------------------------------------------------------------ очистка

WIPE_TABLES = ["stats_versionstat", "inventories_itemstatuschange", "inventories_item", "inventories_rule",
               "inventories_reconciliation", "inventories_inventory", "inventories_sourcefile",
               "inventories_objecttype", "orgs_foldermapping", "orgs_node", "imports_scanfile", "imports_scanrun",
               "imports_acceptjob", "django_q_task", "django_q_ormq"]


def stop_scans(wait=20):
    """Попросить идущие обходы остановиться и дождаться (до wait секунд)."""
    import time
    ScanRun.objects.filter(finished_at__isnull=True).update(cancel=True)
    t = time.time()
    while ScanRun.objects.filter(finished_at__isnull=True).exists() and time.time() - t < wait:
        time.sleep(0.5)
    return not ScanRun.objects.filter(finished_at__isnull=True).exists()


def wipe_all():
    """Снести всё загруженное (пользователь 16.10.2026: «Добавь мне кнопку снести все»):
    описи, позиции, узлы, файлы, обходы, своды, журналы обходов. Остаются учётные
    записи, статусы и группы кодов. Идущий обход сначала останавливается."""
    import shutil
    stopped = stop_scans()
    with transaction.atomic():
        with connection.cursor() as c:
            c.execute("SET LOCAL statement_timeout = '600s'")
            c.execute("TRUNCATE " + ", ".join(WIPE_TABLES) + " RESTART IDENTITY CASCADE")
    shutil.rmtree(str(settings.IMPORT_LOG_DIR), ignore_errors=True)
    return stopped

