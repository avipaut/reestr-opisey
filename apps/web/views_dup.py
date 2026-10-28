"""«Дубли описей» — группы похожих описей одного учреждения и что из них не вошло в
свод (apps.imports.dedup; решение пользователя 28.10.2026: обработка дублей —
объединение множеств похожих описей, вторым этапом импорта; итог — «пускай в вебе
показывается. С возможностью выгрузки оттуда»).

Повтор строки другой описи группы в свод не входит. Группы пересобираются после
каждого обхода — правка здесь не нужна, страница показывает и выгружает."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone

from apps.inventories.models import DupGroup, Inventory, Item
from apps.orgs.models import Node

from .xlsx import write_xlsx, xlsx_file

PER_PAGE = 50


def _groups(q):
    qs = (DupGroup.objects.select_related("node", "base__source_file")
          .annotate(n=Count("inventories"), gone=F("cost_before") - F("cost_after"))
          .order_by("-gone", "-rows_before"))
    if q:
        qs = qs.filter(Q(node__name__icontains=q) | Q(node__code=q))
    return qs


def _members(group):
    """Описи группы с разбивкой строк: повторы по номеру и по наименованию, свои, сверить."""
    invs = list(Inventory.objects.filter(dup_group=group).select_related("source_file").defer(
        "summary_lines", "unknown_columns", "proposed_path").annotate(
        s1=Count("items", filter=Q(items__dup_step=1)), s2=Count("items", filter=Q(items__dup_step=2)),
        chk=Count("items", filter=~Q(items__dup_check="")),
        dup_cost=Sum("items__cost_initial", filter=Q(items__dup_of__isnull=False, items__excluded=False,
                                                      items__held=False))).order_by("-rows", "pk"))
    for i in invs:
        i.own = i.rows - i.s1 - i.s2
        i.is_base = i.pk == group.base_id
        url = reverse("inventory_detail", args=[i.pk])
        i.urls = {k: f"{url}?dup={k}" for k in ("dup", "own", "check")}
    return invs


@login_required
def dup_list(request):
    q = (request.GET.get("q") or "").strip()
    groups = _groups(q)
    if request.GET.get("export") == "xlsx":
        return _export_list(groups, q, request)
    agg = DupGroup.objects.aggregate(groups=Count("id"), rb=Sum("rows_before"), ra=Sum("rows_after"),
                                     cb=Sum("cost_before"), ca=Sum("cost_after"))
    invs = Inventory.objects.filter(dup_group__isnull=False).aggregate(n=Count("id"), dup=Sum("rows_dup"))
    check = Item.objects.filter(inventory__dup_group__isnull=False).exclude(dup_check="").count()
    page = Paginator(groups, PER_PAGE).get_page(request.GET.get("page"))
    Node.fill_trails(g.node for g in page)
    return render(request, "web/dups.html", {
        "page": page, "q": q, "agg": agg, "invs": invs, "check": check,
        "gone": (agg["cb"] or 0) - (agg["ca"] or 0), "pager_qs": f"q={q}" if q else "", "nav": "dups"})


@login_required
def dup_detail(request, pk):
    group = get_object_or_404(DupGroup.objects.select_related("node", "base__source_file"), pk=pk)
    Node.fill_trails([group.node])
    members = _members(group)
    if request.GET.get("export") == "xlsx":
        return _export_group(group, members, request)
    check = (Item.objects.filter(inventory__dup_group=group).exclude(dup_check="")
             .select_related("inventory__source_file").order_by("inv_key", "inventory_id")[:200])
    return render(request, "web/dup_detail.html", {
        "g": group, "members": members, "check": check, "gone": group.cost_before - group.cost_after,
        "dups": sum(i.rows_dup for i in members),
        "nav": "dups"})


def _params(request, extra):
    return [("Параметр", "Значение"), ("Вид", "Дубли описей"), ("Описи", "актуальные"), *extra,
            ("Правило", "описи одного учреждения связываются, если строк меньшей, найденных в большей, не меньше "
                        "порога; повтор строки другой описи группы в свод не входит"),
            ("Кто выгрузил", request.user.get_username()),
            ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]


def _export_list(groups, q, request):
    groups = list(groups)
    Node.fill_trails(g.node for g in groups)
    members = (Inventory.objects.filter(dup_group__in=[g.pk for g in groups]).select_related("source_file", "node")
               .only("dup_group_id", "rows", "rows_dup", "node", "source_file__name", "source_file__path",
                     "loaded_via", "loaded_from").order_by("dup_group_id", "-rows"))
    trail = {g.pk: g.node.trail for g in groups}
    base = {g.pk: g.base_id for g in groups}
    return xlsx_file("Дубли описей", write_xlsx([
        ("Группы", ["Группа", "Учреждение", "Описей", "Основа (больше всего строк)", "Строк во всех описях",
                    "Строк после объединения", "Повторов (не в своде)", "Первонач. стоимость во всех описях, сом",
                    "После объединения, сом", "Не в своде, сом"],
         lambda: ([g.pk, g.node.trail, g.n, g.base.source_file.name if g.base else "", g.rows_before, g.rows_after,
                   g.rows_before - g.rows_after, g.cost_before, g.cost_after, g.gone] for g in groups),
         {"filter": True, "widths": {2: 45, 4: 50, 8: 22, 9: 22, 10: 22}}),
        ("Описи", ["Группа", "Учреждение", "Файл описи", "Основа", "Строк", "Повторов (не в своде)", "Путь к файлу"],
         lambda: ([i.dup_group_id, trail.get(i.dup_group_id, ""), i.source_file.name,
                   "да" if base.get(i.dup_group_id) == i.pk else "", i.rows, i.rows_dup, i.loaded_where]
                  for i in members),
         {"filter": True, "widths": {2: 45, 3: 50, 7: 80}}),
        ("Параметры", None, lambda: _params(request, [("Учреждение содержит", q or "все"), ("Групп", len(groups))]),
         {"widths": {1: 26, 2: 80}})]))


def _export_group(group, members, request):
    """Позиции группы — по строке, с отметкой: своя, повтор (чьей строки), сверить."""
    names = {i.pk: i.source_file.name for i in members}
    items = list(Item.objects.filter(inventory__dup_group=group).select_related("dup_of").only(
        "inventory_id", "sheet", "row_no", "name", "inv_no", "qty", "cost_initial", "excluded", "held",
        "dup_step", "dup_check", "dup_of__inventory_id", "dup_of__sheet", "dup_of__row_no")
        .order_by("inventory_id", "sheet", "row_no"))
    step = {1: "по инв. номеру", 2: "по наименованию"}
    check = {"cost": "тот же инв. номер с другой стоимостью", "qty": "тот же инв. номер с другим количеством"}

    def rows():
        for it in items:
            of = it.dup_of
            yield [names.get(it.inventory_id, ""), it.sheet, it.row_no, it.name, it.inv_no, it.qty, it.cost_initial,
                   "повтор — не в своде" if of else ("мера — не в своде" if it.excluded else
                                                    "подозрительная — не в своде" if it.held else "в своде"),
                   f"{names.get(of.inventory_id, '')}, лист {of.sheet}, строка {of.row_no}" if of else "",
                   step.get(it.dup_step, ""), check.get(it.dup_check, "")]

    return xlsx_file(f"Дубли описей — группа {group.pk}", write_xlsx([
        ("Описи", ["Файл описи", "Основа", "Строк", "Повторов по инв. номеру", "Повторов по наименованию",
                   "Своих строк", "Сверить", "Стоимость повторов, сом", "Путь к файлу"],
         lambda: ([i.source_file.name, "да" if i.is_base else "", i.rows, i.s1, i.s2, i.own, i.chk, i.dup_cost or 0,
                   i.loaded_where] for i in members), {"filter": True, "widths": {1: 50, 9: 80}}),
        ("Позиции", ["Файл описи", "Лист", "Строка", "Основное средство", "Инв. номер", "Количество",
                     "Первонач. стоимость, сом", "В своде", "Повтор строки", "Как найдена пара", "Сверить"],
         rows, {"filter": True, "widths": {1: 40, 4: 45, 8: 22, 9: 45, 10: 16, 11: 36}}),
        ("Параметры", None, lambda: _params(request, [("Учреждение", group.node.trail), ("Группа", group.pk),
                                                      ("Описей", len(members))]), {"widths": {1: 26, 2: 80}})]))
