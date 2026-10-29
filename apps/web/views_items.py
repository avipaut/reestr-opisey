"""Импорт, реестр описей, опись с позициями, поиск позиций, карточка позиции."""

import os
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.imports import services as imports
from apps.imports.models import ScanRun
from apps.inventories.models import Inventory, Item, ItemStatusChange, Status
from apps.orgs.models import Node
from apps.stats import services as stats

from .filters import InventoryFilter, ItemFilter, choices, inventory_choices
from .templatetags.num import num
from .xlsx import items_xlsx_response, write_xlsx, xlsx_file

PAGE = 100


def _page(request, qs, size=PAGE, count=None):
    """Страница выборки; count — число строк, уже посчитанное итогами (ещё один
    проход по позициям — секунда на поиске по всей базе)."""
    p = Paginator(qs, size)
    if count is not None:
        p.count = count
    return p.get_page(request.GET.get("page"))


def _totals(qs):
    return qs.aggregate(rows=Count("id"), qty=Sum("qty"), cost=Sum("cost_initial"), bal=Sum("cost_balance"))


# ------------------------------------------------------------------ импорт

@login_required
def import_page(request):
    from .views_folders import recent_folders
    roots = [{"path": r, "ok": os.path.isdir(r)} for r in settings.INVENTORY_ROOTS]
    return render(request, "web/import.html", {
        "roots": roots, "runs": ScanRun.objects.all()[:5], "recent": recent_folders(),
        "exts": settings.INVENTORY_EXTENSIONS, "processed": settings.PROCESSED_FOLDERS,
        "running": ScanRun.objects.filter(finished_at__isnull=True).first(), "nav": "import"})


# ------------------------------------------------------------------ описи

@login_required
def inventories(request):
    f = InventoryFilter(request)
    if request.GET.get("export") == "xlsx":
        return _inventories_xlsx(f, request)
    ordered = f.ordered()
    tot = {"n": len(ordered), "rows": sum(x["rows"] for _, x in ordered), "qty": sum(x["qty"] for _, x in ordered),
           "cost": sum(x["ci"] for _, x in ordered), "out": sum(x["out"] for _, x in ordered)}
    page = _page(request, ordered, 50)
    by_id = f.qs.in_bulk([i for i, _ in page.object_list])
    page.object_list = [by_id[i] for i, _ in page.object_list]
    for inv, (_, x) in zip(page.object_list, ordered[page.start_index() - 1:page.end_index()]):
        inv.f = x
    Node.fill_trails(inv.node for inv in page.object_list)
    qs_base = request.GET.copy()
    qs_base.pop("export", None)
    return render(request, "web/inventories.html", {
        "page": page, "tot": tot, "f": f, "node": f.node, "q": f.q, "date": f.date, "flags": f.flags, **inventory_choices(),
        "sorts": {k: f.sort_url(k) for k in f.SORTS}, "qs_base": qs_base, "nav": "inventories"})


def _inventories_xlsx(f, request):
    """Список описей выборки в Excel — по строке на опись, со всеми условиями отбора."""
    ordered = f.ordered()
    by_id = f.qs.defer("summary_lines", "unknown_columns", "proposed_path").in_bulk([i for i, _ in ordered])
    invs = []
    for i, x in ordered:
        by_id[i].f = x
        invs.append(by_id[i])
    Node.fill_trails(inv.node for inv in invs)
    headers = ["Учреждение", "Файл", "Позиций", "Количество, шт.", "Первонач. стоимость, сом", "Не учтено, позиций",
               "Строк в описи", "Балансовая стоимость (свод по умолчанию), сом", "Вид источника", "Основа стоимостей", "ПРОВЕРИТЬ", "Дата источника",
               "Принята", "Версий у узла", "Путь к файлу", "Исключено (меры)", "Подозрительные (не в своде)", "Без кода", "Кодов учёта",
               "Накоп. износ, сом", "Загружена", "Как загружена", "Откуда загружена", "Обход №"]

    def rows():
        for inv in invs:
            yield [inv.node.trail if inv.node else "", inv.source_file.name, inv.f["rows"], inv.f["qty"], inv.f["ci"], inv.f["out"],
                   inv.rows, inv.cost_balance, inv.source_kind, inv.cost_basis, "да" if inv.check_flag else "",
                   inv.source_date.strftime("%d.%m.%Y") if inv.source_date else "",
                   timezone.localtime(inv.valid_from).strftime("%d.%m.%Y %H:%M") if inv.valid_from else "",
                   inv.versions, inv.source_file.path, inv.rows_excluded, inv.rows_held, inv.rows_no_code, inv.codes,
                   inv.depreciation, timezone.localtime(inv.parsed_at).strftime("%d.%m.%Y %H:%M"),
                   inv.get_loaded_via_display(), inv.loaded_where, inv.scan_run_id or ""]

    params = [("Параметр", "Значение"), ("Вид", "Описи"), *f.params(), ("Описей", len(invs)),
              ("Кто выгрузил", request.user.get_username()), ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]
    return xlsx_file("Описи", write_xlsx([("Описи", headers, rows, {"filter": True, "widths": {1: 40, 2: 40, 7: 30, 13: 60, 20: 60}}),
                                          ("Параметры", None, lambda: params, {"widths": {1: 26, 2: 80}})]))


@login_required
def inventory_detail(request, pk):
    inv = get_object_or_404(Inventory.objects.select_related("node", "source_file", "decided_by", "loaded_by",
                                                             "scan_run"), pk=pk)
    f = ItemFilter(request, base=inv.items.all())
    qs = f.qs.select_related("status", "object_type").order_by("sheet", "row_no")
    if request.GET.get("export") == "xlsx":
        trail = inv.node.trail if inv.node else ""
        return items_xlsx_response(f"Опись {inv.node.label if inv.node else inv.pk}", qs,
                                   [("Опись", inv.source_file.name), ("Узел", trail or "—"), *f.params()[1:]],
                                   request, trails={inv.pk: trail})
    versions = (Inventory.objects.filter(node=inv.node, state=Inventory.State.ACCEPTED).order_by("-valid_from")
                if inv.node_id else [])
    tot = f.totals()
    return render(request, "web/inventory_detail.html", {
        "inv": inv, "f": f, "page": _page(request, qs, count=tot["rows"]), "tot": tot, "versions": versions,
        "rules": inv.rules.all(), "breakdown": _breakdown(inv, f.flags), "flags": f.flags,
        "invtot": _inv_totals(inv, f.flags), **choices(),
        "keep_inputs": [(k, v) for k in request.GET if k not in ("inc", "page", "export") for v in request.GET.getlist(k)],
        "nav": "inventories"})


def _inv_totals(inv, flags):
    """Итоги описи целиком по флагам «учитывать» (без отборов страницы)."""
    inc = flags.item_q()
    agg = inv.items.aggregate(rows=Count("id", filter=inc), qty=Sum("qty", filter=inc),
                              ci=Sum("cost_initial", filter=inc), dep=Sum("depreciation", filter=inc),
                              cb=Sum("cost_balance", filter=inc), all=Count("id"))
    agg["out"] = agg["all"] - agg["rows"]
    return agg


def _breakdown(inv, flags):
    """Статистика описи: по коду учёта, статусу, листу — по флагам «учитывать»; «не учтено» —
    позиции видов, которые флаги оставили за счётом."""
    inc = flags.item_q()

    def by(field):
        rows = list(inv.items.values(k=F(field)).annotate(
            rows=Count("id", filter=inc), all=Count("id"), qty=Sum("qty", filter=inc),
            cost=Sum("cost_initial", filter=inc), bal=Sum("cost_balance", filter=inc)).order_by("k"))
        for r in rows:
            r["out"] = r["all"] - r["rows"]
        return rows

    return [("По коду учёта", "Код", by("recommended_code")), ("По статусу", "Статус", by("status__name")),
            ("По листу", "Лист", by("sheet"))]


# ------------------------------------------------------------------ поиск

@login_required
def items_search(request):
    f = ItemFilter(request)
    qs = f.qs.select_related("status", "object_type").order_by("inventory__node__path", "sheet", "row_no")
    if request.GET.get("export") == "xlsx":
        # Опись к каждой строке не тянется: у описи тяжёлые поля, а описей в выборке
        # немного — цепочки узлов по описям одним запросом (было 90 с на 99 тыс. строк).
        invs = list(Inventory.objects.filter(pk__in=f.qs.values("inventory_id")).select_related("node")
                    .only("id", "node"))
        Node.fill_trails(i.node for i in invs)
        return items_xlsx_response("Выборка позиций", qs, f.params(), request,
                                   trails={i.pk: i.node.trail if i.node else "" for i in invs})
    ctx = {"f": f, "flags": f.flags, **choices(), "nav": "items"}
    if f.active:
        tot = f.totals()
        page = _page(request, qs.select_related("inventory__node"), count=tot["rows"])
        Node.fill_trails(it.inventory.node for it in page)
        ctx.update(page=page, tot=tot)
    return render(request, "web/items.html", ctx)


@login_required
def item_detail(request, pk):
    it = get_object_or_404(Item.objects.select_related("inventory__node", "inventory__source_file", "status",
                                                       "object_type"), pk=pk)
    # Суммы и количество — с разрядами, как в таблицах («3 557,47», а не «3557,47»; «1», а не «1,000»).
    fields = [(f.verbose_name, num(v, 2 if f.decimal_places == 2 else None) if isinstance(v, Decimal) else v)
              for f in Item._meta.fields if f.name not in ("id", "inventory", "row_hash", "extra", "inv_key", "search")
              for v in [getattr(it, f.name)]]
    # «← к результатам» — только свой адрес (иначе ссылка могла бы увести на чужой сайт)
    back = request.GET.get("back", "")
    if not (back.startswith("/") and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()})):
        back = ""
    return render(request, "web/item_detail.html", {
        "it": it, "fields": fields, "extra": it.extra, "statuses": Status.objects.filter(is_active=True),
        "changes": it.status_changes.select_related("old", "new", "by").order_by("-at"), "nav": "items",
        "back": back})


@login_required
@permission_required("inventories.change_item", raise_exception=True)
@require_POST
def item_status(request, pk):
    """Правка статуса администратором (ТЗ 4.2): журнал и пересчёт свода версии."""
    it = get_object_or_404(Item.objects.select_related("inventory"), pk=pk)
    new = get_object_or_404(Status, pk=request.POST.get("status"))
    if new.pk == it.status_id:
        messages.info(request, "Статус не изменился")
        return redirect("item_detail", pk=pk)
    with transaction.atomic():
        ItemStatusChange.objects.create(item=it, old_id=it.status_id, new=new, by=request.user,
                                        comment=request.POST.get("comment", "")[:2000])
        it.status = new
        it.save(update_fields=["status"])
        if it.inventory.state == Inventory.State.ACCEPTED and it.inventory.is_current:
            stats.restat(it.inventory, timezone.now())
    messages.success(request, f"Статус изменён: {new}")
    return redirect("item_detail", pk=pk)
