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
    tot = Inventory.objects.filter(is_current=True).aggregate(n=Count("id"), rows=Sum("rows"), qty=Sum("qty"),
                                                              cost=Sum("cost_initial"))
    recent = list(Inventory.objects.filter(state=Inventory.State.ACCEPTED).select_related("node", "source_file")
                  .defer("summary_lines", "unknown_columns", "proposed_path").order_by("-valid_from")[:8])
    Node.fill_trails(inv.node for inv in recent)
    return render(request, "web/home.html", {
        "tot": tot, "orgs": Node.objects.filter(parent__isnull=True).count(),
        "pending": Inventory.objects.filter(state=Inventory.State.PENDING).count(),
        "last_run": ScanRun.objects.defer("log").first(),
        "running": ScanRun.objects.filter(finished_at__isnull=True).only("id", "started_at", "roots").first(),
        "recent": recent, "by_status": _by_status(timezone.now(), None, []),
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

def _by_code_table(at, node):
    rows = stats.by_code(at, node)
    order = _status_order()
    statuses = order + sorted({r["status__name"] for r in rows if r["status__name"] not in order})
    statuses = [s for s in statuses if any(r["status__name"] == s for r in rows)]
    by = defaultdict(dict)
    for r in rows:
        by[r["code"]][r["status__name"]] = r["qty"]
    table = []
    for code in sorted(by, key=lambda c: (c in ("", "0"), c)):
        vals = [by[code].get(s, Decimal(0)) for s in statuses]
        table.append({"code": code or "—", "vals": vals, "total": sum(vals)})
    totals = [sum(t["vals"][i] for t in table) for i in range(len(statuses))]
    groups = [{"name": g.name, "prefixes": g.prefixes, "vals": v, "total": sum(v)} for g, v in
              stats.group_sums(stats.code_groups(), {t["code"]: t["vals"] for t in table}, len(statuses))]
    return statuses, table, totals, sum(totals), groups


@login_required
def stats_codes(request):
    at, date_raw = _when(request)
    node = _node(request)
    statuses, table, totals, grand, groups = _by_code_table(at, node)
    if request.GET.get("export") == "xlsx":
        rows = ([[f"Σ {g['name']}", *g["vals"], g["total"]] for g in groups]
                + [[t["code"], *t["vals"], t["total"]] for t in table])
        rows = [[r[0] + (", шт." if r[0].startswith("Σ") else ""), *r[1:]] for r in rows]
        return xlsx_response("Свод по кодам", ["Код учёта", *[f"{s}, шт." for s in statuses], "Итого, шт."], rows,
                             ["Итого", *totals, grand], _params(request, at, node, "Свод по кодам")
                             + [(f"Σ {g['name']}", f"сумма кодов, начинающихся с {g['prefixes']}") for g in groups])
    return render(request, "web/stats_codes.html", {
        "statuses": statuses, "table": table, "totals": totals, "grand": grand, "groups": groups, "date": date_raw,
        "asof": at, "node": node, "crumbs": _crumbs(node), "nav": "codes"})


@login_required
def stats_orgs(request):
    at, date_raw = _when(request)
    parent = _node(request, "parent")
    sel = _statuses(request)
    children = list(Node.objects.filter(parent=parent).order_by("path"))
    cells = defaultdict(lambda: defaultdict(Decimal))
    codes = set()
    # Один запрос по поддереву parent: строка свода — ребёнку, в чьём поддереве её узел
    # (прежде — запрос на каждого ребёнка: 44 у учреждений).
    child_of = {n.path: n.pk for n in children}
    depth = (parent.path if parent else "/").count("/")
    base = stats.stats_at(at, parent).filter(status__in=sel) if sel else stats.stats_at(at, parent)
    for r in base.values("node__path", "code").annotate(q=Sum("qty")):
        cid = child_of.get("/".join(r["node__path"].split("/")[:depth + 1]) + "/")
        if cid is not None:
            cells[cid][r["code"]] += r["q"] or 0
            codes.add(r["code"])
    codes = sorted(codes, key=lambda c: (c in ("", "0"), c))
    has_kids = set(Node.objects.filter(parent__in=children).values_list("parent_id", flat=True))
    table = []
    groups = stats.code_groups()
    for n in children:
        vals = [cells[n.pk].get(c, Decimal(0)) for c in codes]
        gvals = [sum(cells[n.pk].get(c, 0) or 0 for c in codes if g.matches(c)) for g in groups]
        table.append({"node": n, "vals": vals, "gvals": gvals, "total": sum(vals), "drill": n.pk in has_kids})
    table = [t for t in table if t["total"]] if request.GET.get("empty") != "1" else table
    totals = [sum(t["vals"][i] for t in table) for i in range(len(codes))]
    gtotals = [sum(t["gvals"][i] for t in table) for i in range(len(groups))]
    if request.GET.get("export") == "xlsx":
        rows = [[t["node"].label, *t["gvals"], *t["vals"], t["total"]] for t in table]
        return xlsx_response("Код × учреждение",
                             ["Учреждение", *[f"Σ {g.name}, шт." for g in groups], *[f"{c or '—'}, шт." for c in codes], "Итого, шт."],
                             rows, ["Итого", *gtotals, *totals, sum(totals)],
                             _params(request, at, parent, "Код × учреждение")
                             + [("Статусы", ", ".join(st.name for st in sel) or "все")]
                             + [(f"Σ {g.name}", f"сумма кодов, начинающихся с {g.prefixes}") for g in groups])
    return render(request, "web/stats_orgs.html", {
        "codes": codes, "table": table, "totals": totals, "grand": sum(totals), "date": date_raw, "asof": at,
        "groups": groups, "gtotals": gtotals, "all_statuses": Status.objects.filter(is_active=True),
        "sel": sel, "sel_ids": {st.pk for st in sel}, "keep": _keep(date_raw, sel),
        "parent": parent, "crumbs": _crumbs(parent), "nav": "orgs"})


SUB_KEYS = ["nodes", "invs", "rows", "excl", "held", "qty", "ci", "dep", "cb"]
SUM_AS = {"qty": "qty", "ci": "cost_initial", "dep": "depreciation", "cb": "cost_balance"}


def _statuses(request):
    """Отбор по статусам (?status=1&status=4); пусто — все статусы."""
    ids = [x for x in request.GET.getlist("status") if x.isdigit()]
    return list(Status.objects.filter(pk__in=ids)) if ids else []


def _keep(date_raw, statuses, empty=False):
    """Условия отбора для ссылок вглубь дерева."""
    from urllib.parse import urlencode
    return urlencode([("date", date_raw)] + [("status", st.pk) for st in statuses] + ([("empty", "1")] if empty else []))


@login_required
def stats_nodes(request):
    """Свод по подведам: у каждого ребёнка узла — узлов с описями, описей, позиций,
    количество и стоимости по его поддереву на дату, когда принята последняя версия;
    по статусам — таблица ниже. Без отбора по статусам — по итогам действовавших
    описей (в т. ч. описей из одних мер — их нет в своде версий); с отбором — по
    своду версий (меры в него не входят). Строки — детям по пути узла."""
    at, date_raw = _when(request)
    parent = _node(request, "parent")
    sel = _statuses(request)
    children = list(Node.objects.filter(parent=parent).order_by("path"))
    child_of = {n.path: n.pk for n in children}
    depth = (parent.path if parent else "/").count("/")
    acc = defaultdict(lambda: dict.fromkeys(SUB_KEYS, 0) | {"last": None})
    sums = {k: Sum(f) for k, f in SUM_AS.items()}
    if sel:
        rows = stats.stats_at(at, parent).filter(status__in=sel).values("node__path").annotate(
            invs=Count("inventory", distinct=True), rows=Sum("rows"), last=Max("valid_from"), **sums)
    else:
        qs = inventories_at(at if date_raw else None)
        if parent:
            qs = qs.filter(node__path__startswith=parent.path)
        rows = qs.values("node__path").annotate(invs=Count("id"), rows=Sum("rows"), excl=Sum("rows_excluded"),
                                                held=Sum("rows_held"), last=Max("valid_from"), **sums)
    for r in rows.order_by():
        path = r["node__path"] or ""
        key = "own" if parent and path == parent.path else child_of.get("/".join(path.split("/")[:depth + 1]) + "/")
        if key is None:
            continue
        a = acc[key]
        r["nodes"] = 1
        for k in SUB_KEYS:
            a[k] += r.get(k) or 0
        a["last"] = max(filter(None, [a["last"], r["last"]]), default=None)
    has_kids = set(Node.objects.filter(parent__in=children).values_list("parent_id", flat=True))
    table = [{"node": n, **acc[n.pk], "drill": n.pk in has_kids} for n in children]
    if request.GET.get("empty") != "1":
        table = [t for t in table if t["invs"]]
    own = acc["own"] if "own" in acc else None
    totals = {k: sum(t[k] for t in table) + (own[k] if own else 0) for k in SUB_KEYS}
    by_status = _by_status(at, parent, sel)
    own_invs = []
    if parent:
        own_invs = list(inventories_at(at if date_raw else None).filter(node=parent).select_related("source_file", "loaded_by")
                        .defer("summary_lines", "unknown_columns", "proposed_path").order_by("source_file__name"))
        if sel:
            has = set(stats.stats_at(at, parent).filter(node=parent, status__in=sel).values_list("inventory_id", flat=True))
            own_invs = [i for i in own_invs if i.pk in has]
    params = _params(request, at, parent, "Свод по подведам") + [
        ("Статусы", ", ".join(st.name for st in sel) or "все")]
    if request.GET.get("export") == "xlsx":
        return _stats_nodes_xlsx(parent, own, table, totals, by_status, sel, params)
    return render(request, "web/stats_nodes.html", {
        "table": table, "own": own, "own_invs": own_invs, "totals": totals, "date": date_raw, "asof": at,
        "by_status": by_status, "all_statuses": Status.objects.filter(is_active=True), "sel_ids": {st.pk for st in sel},
        "sel": sel, "keep": _keep(date_raw, sel, request.GET.get("empty") == "1"),
        "parent": parent, "crumbs": _crumbs(parent), "nav": "subs"})


def _by_status(at, node, sel):
    """Статистика по статусам по поддереву узла на дату — по своду версий (без мер):
    описей, позиций, количество, стоимости, доля в количестве."""
    qs = stats.stats_at(at, node)
    if sel:
        qs = qs.filter(status__in=sel)
    rows = list(qs.values("status_id", "status__name").annotate(
        invs=Count("inventory", distinct=True), rows=Sum("rows"), **{k: Sum(f) for k, f in SUM_AS.items()}))
    order = {name: i for i, name in enumerate(_status_order())}
    rows.sort(key=lambda r: (order.get(r["status__name"], len(order)), r["status__name"] or ""))
    total_qty = sum(r["qty"] or 0 for r in rows)
    for r in rows:
        r["share"] = (r["qty"] or 0) * 100 / total_qty if total_qty else 0
    return rows


def _stats_nodes_xlsx(parent, own, table, totals, by_status, sel, params):
    from .xlsx import write_xlsx, xlsx_file

    def when(v):
        return timezone.localtime(v).strftime("%d.%m.%Y %H:%M") if v else ""

    keys = [k for k in SUB_KEYS if not (sel and k in ("excl", "held"))]
    heads = {"nodes": "Узлов с описями", "invs": "Описей", "rows": "Позиций", "excl": "Из них мер (не в своде)", "held": "Подозрительные (не в своде до «Верно»)",
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
            yield [r["status__name"] or "без статуса", r["invs"], r["rows"], r["qty"], r["ci"], r["dep"], r["cb"],
                   round(r["share"], 2)]

    return xlsx_file("Свод по подведам", write_xlsx([
        ("Подведы", ["Подвед" if parent else "Учреждение", *[heads[k] for k in keys], "Последняя версия принята"],
         rows, {"filter": True, "widths": {1: 50}}),
        ("По статусам", ["Статус", "Описей", "Позиций", "Количество, шт.", "Первонач. стоимость, сом",
                         "Накоп. износ, сом", "Балансовая стоимость, сом", "Доля в количестве, %"],
         status_rows, {"filter": True, "widths": {1: 30}}),
        ("Параметры", None, lambda: [("Параметр", "Значение"), *params], {"widths": {1: 26, 2: 80}})]))


def _params(request, at, node, view):
    return [("Вид", view), ("На дату", timezone.localtime(at).strftime("%d.%m.%Y %H:%M")),
            ("Учреждение", " › ".join(n.label for n in _crumbs(node)) or "все"),
            ("Величина", "количество, шт. — сумма столбца «Количество»; где в нём стояла сумма денег "
                         "(равна цене или больше 1000) — 1 шт.; меры (материалы, запасы: дробное количество, "
                         "единица вместо названия) не входят"),
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
