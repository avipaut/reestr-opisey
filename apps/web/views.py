"""Экраны первого этапа (ТЗ 1.5, 5.2): описи на просмотр, просмотр и приём,
свод по кодам, код × учреждение, журнал обходов."""

import datetime as dt
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.imports import services as imports
from apps.imports.models import ScanRun
from apps.inventories.models import Inventory, Status
from apps.orgs.models import Node
from apps.stats import services as stats

from .compare import compare
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


# ------------------------------------------------------------------ просмотр

@login_required
def review_list(request):
    pending = (Inventory.objects.filter(state=Inventory.State.PENDING)
               .select_related("source_file", "node").order_by("parsed_at"))
    errors = (Inventory.objects.filter(state=Inventory.State.ERROR)
              .select_related("source_file").order_by("-parsed_at")[:20])
    from apps.imports.models import AcceptJob
    page = Paginator(pending, 200).get_page(request.GET.get("page"))
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
    if settings.DEBUG and request.POST.get("sync", "1") == "1":
        run = imports.scan()
        messages.info(request, f"Обход: {run.get_state_display()}, новых на просмотр — {run.pending}, "
                               f"ошибок — {run.errors}")
    else:
        from django_q.tasks import async_task
        async_task("apps.imports.tasks.scan_task")
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
    return statuses, table, totals, sum(totals)


@login_required
def stats_codes(request):
    at, date_raw = _when(request)
    node = _node(request)
    statuses, table, totals, grand = _by_code_table(at, node)
    if request.GET.get("export") == "xlsx":
        rows = [[t["code"], *t["vals"], t["total"]] for t in table]
        return xlsx_response("Свод по кодам", ["Код учёта", *statuses, "Итого"], rows,
                             ["Итого", *totals, grand], _params(request, at, node, "Свод по кодам"))
    return render(request, "web/stats_codes.html", {
        "statuses": statuses, "table": table, "totals": totals, "grand": grand, "date": date_raw,
        "asof": at, "node": node, "crumbs": _crumbs(node), "nav": "codes"})


@login_required
def stats_orgs(request):
    at, date_raw = _when(request)
    parent = _node(request, "parent")
    children = list(Node.objects.filter(parent=parent).order_by("path"))
    cells = defaultdict(dict)
    codes = set()
    for n in children:
        for r in stats.stats_at(at, n).values("code").annotate(q=Sum("qty")):
            cells[n.pk][r["code"]] = r["q"]
            codes.add(r["code"])
    codes = sorted(codes, key=lambda c: (c in ("", "0"), c))
    has_kids = set(Node.objects.filter(parent__in=children).values_list("parent_id", flat=True))
    table = []
    for n in children:
        vals = [cells[n.pk].get(c, Decimal(0)) for c in codes]
        table.append({"node": n, "vals": vals, "total": sum(vals), "drill": n.pk in has_kids})
    table = [t for t in table if t["total"]] if request.GET.get("empty") != "1" else table
    totals = [sum(t["vals"][i] for t in table) for i in range(len(codes))]
    if request.GET.get("export") == "xlsx":
        rows = [[t["node"].label, *t["vals"], t["total"]] for t in table]
        return xlsx_response("Код × учреждение", ["Учреждение", *[c or "—" for c in codes], "Итого"], rows,
                             ["Итого", *totals, sum(totals)], _params(request, at, parent, "Код × учреждение"))
    return render(request, "web/stats_orgs.html", {
        "codes": codes, "table": table, "totals": totals, "grand": sum(totals), "date": date_raw, "asof": at,
        "parent": parent, "crumbs": _crumbs(parent), "nav": "orgs"})


def _params(request, at, node, view):
    return [("Вид", view), ("На дату", timezone.localtime(at).strftime("%d.%m.%Y %H:%M")),
            ("Учреждение", " › ".join(n.label for n in _crumbs(node)) or "все"),
            ("Величина", "количество (с учётом столбца «Количество»)"),
            ("Кто выгрузил", request.user.get_username()),
            ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]


# ------------------------------------------------------------------ журнал

@login_required
def scans(request):
    return render(request, "web/scans.html", {"runs": ScanRun.objects.all()[:100], "nav": "scans"})


@login_required
def scan_detail(request, pk):
    run = get_object_or_404(ScanRun, pk=pk)
    found = (Inventory.objects.filter(parsed_at__gte=run.started_at, state=Inventory.State.PENDING)
             .select_related("source_file", "node").order_by("parsed_at")[:500]) if run.finished_at else []
    return render(request, "web/scan_detail.html", {"run": run, "found": found, "nav": "import"})


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
