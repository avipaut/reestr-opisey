"""Экраны первого этапа (ТЗ 1.5, 5.2): описи на просмотр, просмотр и приём,
свод по кодам, код × учреждение, журнал обходов."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Sum
from django.db.models.functions import Length, Substr
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.imports import dedup, services as imports
from apps.imports.models import ScanRun
from apps.inventories.models import Inventory, Status
from apps.orgs.models import Node
from apps.stats import services as stats
from apps.stats.flags import Flags

from .compare import compare
from .filters import inventories_at
from .xlsx import xlsx_response


# ------------------------------------------------------------------ отбор

def _when(request):
    """Дата «на дату»: конец выбранных суток по местному времени; без даты — сейчас."""
    raw = request.GET.get("date") or ""
    try:
        d = dt.date.fromisoformat(raw)
    except ValueError:
        return timezone.now(), ""
    end = dt.datetime.combine(d + dt.timedelta(days=1), dt.time.min)
    return timezone.make_aware(end) - dt.timedelta(microseconds=1), raw


def _node(request, key="node"):
    pk = request.GET.get(key)
    return Node.objects.filter(pk=pk).first() if pk and pk.isdigit() else None


def _crumbs(node):
    out = []
    while node is not None:
        out.append(node)
        node = node.parent
    return list(reversed(out))


def _status_order():
    return list(Status.objects.filter(is_active=True).values_list("name", flat=True))


# ------------------------------------------------------------------ обзор

@login_required
def home(request):
    """Стартовая страница: поиск позиций, итоги, последний обход, что требует внимания.
    Прежде стартовой была «На просмотр» — при автоприёме она почти всегда пуста."""
    flags = Flags.from_request(request)
    now = timezone.now()
    tot = _unx(stats.stats_at(now, flags=None).aggregate(**_flag_sums(flags)))
    recent = list(Inventory.objects.filter(state=Inventory.State.ACCEPTED).select_related("node", "source_file")
                  .defer("summary_lines", "unknown_columns", "proposed_path").order_by("-valid_from")[:8])
    Node.fill_trails(inv.node for inv in recent)
    return render(request, "web/home.html", {
        "tot": tot, "orgs": Node.objects.filter(parent__isnull=True).count(),
        "pending": Inventory.objects.filter(state=Inventory.State.PENDING).count(),
        "last_run": ScanRun.objects.defer("log").first(),
        "running": ScanRun.objects.filter(finished_at__isnull=True).only("id", "started_at", "roots").first(),
        "recent": recent, "by_status": _by_status(now, None, [], flags), "flags": flags,
        "held": Inventory.objects.filter(is_current=True, rows_held__gt=0).aggregate(n=Count("id"), rows=Sum("rows_held")),
        "nav": "home"})


# ------------------------------------------------------------------ просмотр

@login_required
def review_list(request):
    pending = (Inventory.objects.filter(state=Inventory.State.PENDING)
               .select_related("source_file", "node").order_by("parsed_at"))
    errors = (Inventory.objects.filter(state=Inventory.State.ERROR)
              .select_related("source_file").order_by("-parsed_at")[:20])
    from apps.imports.models import AcceptJob
    page = Paginator(pending, 200).get_page(request.GET.get("page"))
    Node.fill_trails(inv.node for inv in page)
    return render(request, "web/review_list.html", {
        "page": page, "pending_total": page.paginator.count,
        "with_node": Inventory.objects.filter(state=Inventory.State.PENDING, node__isnull=False).count(),
        "without_node": Inventory.objects.filter(state=Inventory.State.PENDING, node__isnull=True).count(),
        "errors": errors, "errors_total": Inventory.objects.filter(state=Inventory.State.ERROR).count(),
        "last_run": ScanRun.objects.first(), "job": AcceptJob.objects.first(), "nav": "review"})


@login_required
@require_POST
def scan_now(request):
    """«Проверить сейчас» — в очередь фоновых задач; в разработке без
    обработчика очереди — сразу (та же функция)."""
    run = ScanRun.objects.create(stage="в очереди", by=request.user)  # кто запустил — в описи
    if settings.DEBUG and request.POST.get("sync", "1") == "1":
        run = imports.scan(run=run)
        messages.info(request, f"Обход: {run.get_state_display()}, новых на просмотр — {run.pending}, "
                               f"ошибок — {run.errors}")
    else:
        from django_q.tasks import async_task
        async_task("apps.imports.tasks.scan_task", None, run.pk)
        messages.info(request, "Обход поставлен в очередь")
    return redirect("review_list")


@login_required
def review_detail(request, pk):
    inv = get_object_or_404(Inventory.objects.select_related("source_file", "node"), pk=pk)
    current = Inventory.objects.filter(source_file=inv.source_file, is_current=True).exclude(pk=inv.pk).first()
    cmp = compare(inv, current) if inv.state in (Inventory.State.PENDING, Inventory.State.ACCEPTED) else None
    totals_before = (current.rows, current.qty, current.cost_initial) if current else None
    return render(request, "web/review_detail.html", {
        "inv": inv, "current": current, "cmp": cmp, "totals_before": totals_before,
        "reconciliations": inv.reconciliations.all(), "nav": "review"})


@login_required
@permission_required("inventories.change_inventory", raise_exception=True)
@require_POST
def review_decide(request, pk):
    inv = get_object_or_404(Inventory, pk=pk)
    action = request.POST.get("action")
    try:
        if action == "accept":
            node = None
            if request.POST.get("node"):
                node = get_object_or_404(Node, pk=request.POST["node"])
            inv = imports.accept(inv, user=request.user, node=node)
            # Состав актуальных узла сменился — его группы похожих описей пересобираются.
            dedup.run(nodes=[inv.node_id])
            messages.success(request, f"Принята: {inv.source_file.name} — актуальная опись узла {inv.node}")
        elif action == "reject":
            imports.reject(inv, user=request.user)
            messages.success(request, f"Отклонена: {inv.source_file.name}")
        else:
            messages.error(request, "Неизвестное действие")
    except ValueError as e:
        messages.error(request, f"Не выполнено: {e}")
        return redirect("review_detail", pk=pk)
    nxt = Inventory.objects.filter(state=Inventory.State.PENDING).order_by("parsed_at").first()
    return redirect("review_detail", pk=nxt.pk) if nxt else redirect("review_list")


# ------------------------------------------------------------------ своды

def _pairs(vals):
    """[(позиций, шт.)] по статусам → (пары, итог-пара)."""
    pairs = [(vals[2 * i], vals[2 * i + 1]) for i in range(len(vals) // 2)]
    return pairs, (sum(p[0] for p in pairs), sum(p[1] for p in pairs))


def _by_code_table(at, node, flags):
    """Код × статус: у каждого статуса позиций и шт. (решение пользователя 29.10.2026 —
    оба столбца везде)."""
    rows = stats.by_code(at, node, flags)
    order = _status_order()
    statuses = order + sorted({r["status__name"] for r in rows if r["status__name"] not in order})
    statuses = [s for s in statuses if any(r["status__name"] == s for r in rows)]
    by = defaultdict(dict)
    for r in rows:
        by[r["code"]][r["status__name"]] = (r["rows"] or 0, r["qty"] or Decimal(0))
    flat = {}
    for code in by:
        flat[code] = [x for s in statuses for x in by[code].get(s, (0, Decimal(0)))]
    table = []
    for code in sorted(by, key=lambda c: (c in ("", "0"), c)):
        pairs, total = _pairs(flat[code])
        table.append({"code": code or "—", "vals": pairs, "total": total})
    totals, grand = _pairs([sum(flat[c][i] for c in flat) for i in range(2 * len(statuses))])
    groups = []
    for g, v in stats.group_sums(stats.code_groups(), flat, 2 * len(statuses)):
        pairs, total = _pairs(v)
        groups.append({"name": g.name, "prefixes": g.prefixes, "vals": pairs, "total": total})
    return statuses, table, totals, grand, groups


@login_required
def stats_codes(request):
    at, date_raw = _when(request)
    node = _node(request)
    flags = Flags.from_request(request)
    statuses, table, totals, grand, groups = _by_code_table(at, node, flags)
    if request.GET.get("export") == "xlsx":
        flat = lambda pairs, total: [x for p in [*pairs, total] for x in p]  # noqa: E731
        rows = ([[f"Σ {g['name']}", *flat(g["vals"], g["total"])] for g in groups]
                + [[t["code"], *flat(t["vals"], t["total"])] for t in table])
        heads = [h for s in [*statuses, "Итого"] for h in (f"{s}, позиций", f"{s}, шт.")]
        return xlsx_response("Свод по кодам", ["Код учёта", *heads], rows,
                             ["Итого", *flat(totals, grand)], _params(request, at, node, "Свод по кодам", flags)
                             + [(f"Σ {g['name']}", f"сумма кодов, начинающихся с {g['prefixes']}") for g in groups])
    return render(request, "web/stats_codes.html", {
        "statuses": statuses, "table": table, "totals": totals, "grand": grand, "groups": groups, "date": date_raw,
        "asof": at, "node": node, "crumbs": _crumbs(node), "flags": flags, "nav": "codes"})


@login_required
def stats_orgs(request):
    at, date_raw = _when(request)
    parent = _node(request, "parent")
    sel = _statuses(request)
    flags = Flags.from_request(request)
    children = list(Node.objects.filter(parent=parent).order_by("path"))
    cells = defaultdict(lambda: defaultdict(lambda: [0, Decimal(0)]))  # [позиций, шт.]
    codes = set()
    # Один запрос по поддереву parent: строка свода — ребёнку, в чьём поддереве её узел
    # (прежде — запрос на каждого ребёнка: 44 у учреждений).
    child_of = {n.path: n.pk for n in children}
    depth = (parent.path if parent else "/").count("/")
    base = stats.stats_at(at, parent, flags)
    if sel:
        base = base.filter(status__in=sel)
    for r in base.values("node__path", "code").annotate(n=Sum("rows"), q=Sum("qty")):
        cid = child_of.get("/".join(r["node__path"].split("/")[:depth + 1]) + "/")
        if cid is not None:
            c = cells[cid][r["code"]]
            c[0] += r["n"] or 0
            c[1] += r["q"] or 0
            codes.add(r["code"])
    codes = sorted(codes, key=lambda c: (c in ("", "0"), c))
    has_kids = set(Node.objects.filter(parent__in=children).values_list("parent_id", flat=True))
    table = []
    groups = stats.code_groups()
    zero = (0, Decimal(0))

    def add(pairs):
        return (sum(p[0] for p in pairs), sum(p[1] for p in pairs))

    for n in children:
        vals = [tuple(cells[n.pk].get(c, zero)) for c in codes]
        gvals = [add([tuple(cells[n.pk][c]) for c in codes if c in cells[n.pk] and g.matches(c)]) for g in groups]
        table.append({"node": n, "vals": vals, "gvals": gvals, "total": add(vals), "drill": n.pk in has_kids})
    table = [t for t in table if t["total"][0]] if request.GET.get("empty") != "1" else table
    totals = [add([t["vals"][i] for t in table]) for i in range(len(codes))]
    gtotals = [add([t["gvals"][i] for t in table]) for i in range(len(groups))]
    grand = add(totals)
    if request.GET.get("export") == "xlsx":
        flat = lambda pairs: [x for p in pairs for x in p]  # noqa: E731
        rows = [[t["node"].label, *flat(t["gvals"]), *flat(t["vals"]), *t["total"]] for t in table]
        heads = [f"Σ {g.name}" for g in groups] + [c or "—" for c in codes] + ["Итого"]
        return xlsx_response("Код × учреждение",
                             ["Учреждение", *[h for c in heads for h in (f"{c}, позиций", f"{c}, шт.")]],
                             rows, ["Итого", *flat(gtotals), *flat(totals), *grand],
                             _params(request, at, parent, "Код × учреждение", flags)
                             + [("Статусы", ", ".join(st.name for st in sel) or "все")]
                             + [(f"Σ {g.name}", f"сумма кодов, начинающихся с {g.prefixes}") for g in groups])
    return render(request, "web/stats_orgs.html", {
        "codes": codes, "table": table, "totals": totals, "grand": grand, "date": date_raw, "asof": at,
        "groups": groups, "gtotals": gtotals, "all_statuses": Status.objects.filter(is_active=True),
        "sel": sel, "sel_ids": {st.pk for st in sel}, "keep": _keep(date_raw, sel, flags=flags), "flags": flags,
        "parent": parent, "crumbs": _crumbs(parent), "nav": "orgs"})


SUB_KEYS = ["nodes", "invs", "rows", "out", "qty", "ci", "dep", "cb"]
SUM_AS = {"qty": "qty", "ci": "cost_initial", "dep": "depreciation", "cb": "cost_balance"}


def _flag_sums(flags):
    """Суммы строк свода: учтённое флагами и «не учтено, позиций» — строки видов, которые
    флаги оставили за счётом."""
    inc = flags.stat_q()
    sums = {f"x_{k}": Sum(f, filter=inc) for k, f in SUM_AS.items()}
    sums["x_rows"] = Sum("rows", filter=inc)
    sums["x_invs"] = Count("inventory", distinct=True, filter=inc)
    sums["x_all"] = Sum("rows")
    return sums


def _unx(r):
    """Строка сумм _flag_sums → обычные имена; out = все строки − учтённые."""
    out = {k[2:] if k.startswith("x_") else k: v for k, v in r.items()}
    out["out"] = (out.pop("all", 0) or 0) - (out.get("rows") or 0)
    return out


def _statuses(request):
    """Отбор по статусам (?status=1&status=4); пусто — все статусы."""
    ids = [x for x in request.GET.getlist("status") if x.isdigit()]
    return list(Status.objects.filter(pk__in=ids)) if ids else []


def _keep(date_raw, statuses, empty=False, flags=None):
    """Условия отбора для ссылок вглубь дерева."""
    from urllib.parse import urlencode
    return urlencode([("date", date_raw)] + [("status", st.pk) for st in statuses] + ([("empty", "1")] if empty else [])
                     + [("inc", k) for k in (flags.inc if flags else [])])


@login_required
def stats_nodes(request):
    """Свод по подведам: у каждого ребёнка узла — узлов с описями, описей, позиций,
    количество и стоимости по его поддереву на дату, когда принята последняя версия;
    по статусам — таблица ниже. Всё — по своду версий и флагам «учитывать»; «не учтено» —
    позиции видов, которые флаги оставили за счётом. Строки — детям по пути узла."""
    at, date_raw = _when(request)
    parent = _node(request, "parent")
    sel = _statuses(request)
    flags = Flags.from_request(request)
    children = list(Node.objects.filter(parent=parent).order_by("path"))
    child_of = {n.path: n.pk for n in children}
    depth = (parent.path if parent else "/").count("/")
    acc = defaultdict(lambda: dict.fromkeys(SUB_KEYS, 0) | {"last": None})
    base = stats.stats_at(at, parent, flags=None)
    if sel:
        base = base.filter(status__in=sel)
    rows = base.values("node__path").annotate(last=Max("valid_from", filter=flags.stat_q()), **_flag_sums(flags))
    for r in rows.order_by():
        r = _unx(r)
        path = r["node__path"] or ""
        key = "own" if parent and path == parent.path else child_of.get("/".join(path.split("/")[:depth + 1]) + "/")
        if key is None:
            continue
        a = acc[key]
        r["nodes"] = 1 if r["invs"] else 0
        for k in SUB_KEYS:
            a[k] += r.get(k) or 0
        a["last"] = max(filter(None, [a["last"], r["last"]]), default=None)
    has_kids = set(Node.objects.filter(parent__in=children).values_list("parent_id", flat=True))
    table = [{"node": n, **acc[n.pk], "drill": n.pk in has_kids} for n in children]
    if request.GET.get("empty") != "1":
        table = [t for t in table if t["invs"] or t["out"]]
    own = acc["own"] if "own" in acc else None
    totals = {k: sum(t[k] for t in table) + (own[k] if own else 0) for k in SUB_KEYS}
    by_status = _by_status(at, parent, sel, flags)
    own_invs = []
    if parent:
        own_invs = list(inventories_at(at if date_raw else None).filter(node=parent).select_related("source_file", "loaded_by")
                        .defer("summary_lines", "unknown_columns", "proposed_path").order_by("source_file__name"))
        sums = stats.inventory_sums(at, [i.pk for i in own_invs], flags, sel)
        own_invs = [i for i in own_invs if i.pk in sums]
        for i in own_invs:
            i.f = sums[i.pk]
    params = _params(request, at, parent, "Свод по подведам", flags) + [
        ("Статусы", ", ".join(st.name for st in sel) or "все")]
    if request.GET.get("export") == "xlsx":
        return _stats_nodes_xlsx(parent, own, table, totals, by_status, params)
    return render(request, "web/stats_nodes.html", {
        "table": table, "own": own, "own_invs": own_invs, "totals": totals, "date": date_raw, "asof": at,
        "by_status": by_status, "all_statuses": Status.objects.filter(is_active=True), "sel_ids": {st.pk for st in sel},
        "sel": sel, "keep": _keep(date_raw, sel, request.GET.get("empty") == "1", flags), "flags": flags,
        "status_keep": flags.query(), "parent": parent, "crumbs": _crumbs(parent), "nav": "subs"})


def _by_status(at, node, sel, flags):
    """Статистика по статусам по поддереву узла на дату — по своду версий и флагам:
    описей, позиций, количество, стоимости, доля в количестве, не учтено."""
    qs = stats.stats_at(at, node, flags=None)
    if sel:
        qs = qs.filter(status__in=sel)
    rows = [_unx(r) for r in qs.values("status_id", "status__name").annotate(**_flag_sums(flags))]
    rows = [r for r in rows if r["rows"] or r["out"]]
    order = {name: i for i, name in enumerate(_status_order())}
    rows.sort(key=lambda r: (order.get(r["status__name"], len(order)), r["status__name"] or ""))
    total_qty = sum(r["qty"] or 0 for r in rows)
    for r in rows:
        r["share"] = (r["qty"] or 0) * 100 / total_qty if total_qty else 0
    return rows


def _stats_nodes_xlsx(parent, own, table, totals, by_status, params):
    from .xlsx import write_xlsx, xlsx_file

    def when(v):
        return timezone.localtime(v).strftime("%d.%m.%Y %H:%M") if v else ""

    keys = SUB_KEYS
    heads = {"nodes": "Узлов с описями", "invs": "Описей", "rows": "Позиций", "out": "Не учтено, позиций",
             "qty": "Количество, шт.", "ci": "Первонач. стоимость, сом", "dep": "Накоп. износ, сом",
             "cb": "Балансовая стоимость, сом"}

    def line(label, t):
        return [label, *[t[k] for k in keys], when(t["last"])]

    def rows():
        if own:
            yield line(f"{parent.label} — свои описи", own)
        for t in table:
            yield line(t["node"].label, t)
        yield ["Итого", *[totals[k] for k in keys], ""]

    def status_rows():
        for r in by_status:
            yield [r["status__name"] or "без статуса", r["invs"], r["rows"], r["out"], r["qty"], r["ci"], r["dep"], r["cb"],
                   round(r["share"], 2)]

    return xlsx_file("Свод по подведам", write_xlsx([
        ("Подведы", ["Подвед" if parent else "Учреждение", *[heads[k] for k in keys], "Последняя версия принята"],
         rows, {"filter": True, "widths": {1: 50}}),
        ("По статусам", ["Статус", "Описей", "Позиций", "Не учтено, позиций", "Количество, шт.", "Первонач. стоимость, сом",
                         "Накоп. износ, сом", "Балансовая стоимость, сом", "Доля в количестве, %"],
         status_rows, {"filter": True, "widths": {1: 30}}),
        ("Параметры", None, lambda: [("Параметр", "Значение"), *params], {"widths": {1: 26, 2: 80}})]))


def _params(request, at, node, view, flags):
    return [("Вид", view), ("На дату", timezone.localtime(at).strftime("%d.%m.%Y %H:%M")),
            ("Учреждение", " › ".join(n.label for n in _crumbs(node)) or "все"),
            ("Величины", "позиций — строк описей; шт. — сумма столбца «Количество» (где в нём стояла сумма "
                         "денег — равна цене или больше 1000 — 1 шт.)"),
            *flags.params(),
            ("Что учтено", flags.summary()),
            ("Кто выгрузил", request.user.get_username()),
            ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]


# ------------------------------------------------------------------ журнал

@login_required
def scans(request):
    """Журнал обходов: от журнала ошибок — первые строки (полный — на странице обхода;
    целиком в таблице страница росла на сотни КБ с каждым обходом)."""
    runs = list(ScanRun.objects.defer("log").annotate(log_start=Substr("log", 1, 1200), log_len=Length("log"))[:100])
    for r in runs:
        lines = [x for x in r.log_start.split("\n") if x]
        r.log_lines = lines[:3]
        r.log_more = r.log_len > len("\n".join(r.log_lines))
    return render(request, "web/scans.html", {"runs": runs, "nav": "scans"})


@login_required
def scan_detail(request, pk):
    run = ScanRun.objects.filter(pk=pk).first()
    if run is None:  # база очищена, а страница хода была открыта — без ошибки 404
        messages.info(request, "Этого обхода больше нет — база очищена. Последние обходы — ниже.")
        return redirect("import_page")
    return render(request, "web/scan_detail.html", {"run": run, "nav": "import"})


@login_required
@permission_required("inventories.change_inventory", raise_exception=True)
@require_POST
def review_bulk(request):
    """«Принять выбранные» — сразу; «Принять все с узлом» — фоновым потоком с ходом."""
    from apps.imports.models import AcceptJob
    if AcceptJob.objects.filter(finished_at__isnull=True).exists():
        messages.error(request, "Приём уже идёт — дождитесь конца")
        return redirect("review_list")
    qs = Inventory.objects.filter(state=Inventory.State.PENDING, node__isnull=False)
    if request.POST.get("action") == "selected":
        ids = [int(x) for x in request.POST.getlist("ids") if x.isdigit()]
        ids = list(qs.filter(pk__in=ids).order_by("parsed_at").values_list("pk", flat=True))
    else:
        ids = list(qs.order_by("parsed_at").values_list("pk", flat=True))
    if not ids:
        messages.error(request, "Нечего принимать: нет описей с определённым узлом")
        return redirect("review_list")
    job = AcceptJob.objects.create(by=request.user, total=len(ids))
    if getattr(settings, "SCAN_SYNC", False) or len(ids) <= 20:
        imports.accept_many(ids, request.user, job)
        messages.success(request, f"Принято: {job.done}" + (f", не принято: {job.failed}" if job.failed else ""))
    else:
        import threading
        from django.db import connection

        def work():
            try:
                imports.accept_many(ids, request.user, job)
            finally:
                connection.close()

        threading.Thread(target=work, daemon=True).start()
        messages.info(request, f"Приём {len(ids)} описей начат — ход вверху страницы")
    return redirect("review_list")
