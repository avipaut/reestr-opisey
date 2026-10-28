"""«Подозрительные» — описи с позициями, где первоначальная стоимость подозрительно
высока или низка или в ней не число (пользователь 28.10.2026: «сделай вкладку
подозрительные где будут описи с подозрительно низким или высоким первоначальной
стоимостью, или где в первоначальной стоимости будут буквы»).

Такие позиции не входят в своды, пока здесь не нажато «Верно» (пользователь
28.10.2026: «сделай так чтобы они не учитывались в основной статистике пока на
вкладке подозрительные я не нажму кнопку верно»). Правила и пороги —
apps.imports.suspects, settings.SUSPECT_HIGH / SUSPECT_LOW."""

from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db.models import Count, Max, Min, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.imports import suspects
from apps.inventories.models import Inventory, Item
from apps.orgs.models import Node

from .xlsx import write_xlsx, xlsx_file

REASONS = sorted(suspects.REASONS, key=lambda r: ["high", "low", "neg", "text"].index(r[0]))  # порядок на экране
SHORT = {"high": "Дорогих", "low": "Дешёвых", "neg": "Отриц.", "text": "С буквами"}  # шапка таблицы


class Suspect:
    """Отбор страницы: что показывать (ждут «Верно» или подтверждённые) и какие причины."""

    def __init__(self, request):
        g = request.GET
        self.ok = g.get("show") == "ok"
        chosen = [c for c in g.getlist("cat") if c in suspects.LABELS]
        self.cats = set(chosen) if g.get("f") == "1" else set(suspects.LABELS)

    def items(self, cats=None):
        state = Q(suspect_ok_at__isnull=False) if self.ok else Q(held=True)
        return Item.objects.filter(state, inventory__is_current=True, suspect__in=cats or self.cats)

    def query(self, extra=()):
        return urlencode([("f", "1")] + ([("show", "ok")] if self.ok else [])
                         + [("cat", c) for c, _ in REASONS if c in self.cats] + list(extra))


def reasons(it):
    out = []
    if it.suspect == "text":
        out.append(f"не число: «{it.cost_initial_raw}»")
    elif it.suspect:
        out.append(suspects.LABELS[it.suspect]
                   + (" — похоже на код счёта с номером" if suspects.looks_like_code(it.cost_initial) else ""))
    if it.excluded:
        out.append("мера — в свод не входит и так")
    return "; ".join(out)


@login_required
def suspect_list(request):
    s = Suspect(request)
    if request.GET.get("export") == "xlsx":
        return _export(s, request)
    # Один проход по отмеченным позициям актуальных описей: по описи — число позиций каждой причины.
    rows = list(s.items(list(suspects.LABELS)).values("inventory_id").annotate(
        **{c: Count("id", filter=Q(suspect=c)) for c, _ in REASONS}, mx=Max("cost_initial"), mn=Min("cost_initial"),
        big=Sum("cost_initial", filter=Q(suspect="high", excluded=False)), measures=Count("id", filter=Q(excluded=True)),
        ok_at=Max("suspect_ok_at")).order_by())
    tiles = [{"key": c, "label": label, "on": c in s.cats, "rows": sum(r[c] for r in rows),
              "invs": sum(1 for r in rows if r[c])} for c, label in REASONS]
    other = Item.objects.filter(Q(suspect_ok_at__isnull=True, held=True) if s.ok else Q(suspect_ok_at__isnull=False),
                                inventory__is_current=True, suspect__in=list(suspects.LABELS)).aggregate(
        rows=Count("id"), invs=Count("inventory", distinct=True))
    rows = [r for r in rows if any(r[c] for c in s.cats)]
    rows.sort(key=lambda r: (-(r["big"] or 0), -(r["mx"] or 0), -sum(r[c] for c in s.cats)))
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    invs = Inventory.objects.select_related("node", "source_file").defer(
        "summary_lines", "unknown_columns", "proposed_path").in_bulk([r["inventory_id"] for r in page])
    Node.fill_trails(i.node for i in invs.values())
    state = "ok" if s.ok else "held"
    for r in page:
        r["inv"] = invs[r["inventory_id"]]
        r["code_like"] = suspects.looks_like_code(r["mx"])
        base = reverse("inventory_detail", args=[r["inventory_id"]])
        r["cells"] = [(r[c], f"{base}?suspect={c}") for c, _ in REASONS if c in s.cats]
        r["all_url"] = f"{base}?suspect={state}"
    return render(request, "web/suspects.html", {
        "s": s, "reasons": REASONS, "shown": [(SHORT[c], label) for c, label in REASONS if c in s.cats],
        "tiles": tiles, "page": page, "total": len(rows), "other": other, "high": suspects.high(),
        "low": suspects.low(), "pager_qs": s.query(), "nav": "suspects"})


def _back(request):
    nxt = request.POST.get("next") or ""
    ok = url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) and nxt.startswith("/")
    return redirect(nxt if ok else "suspects")


@login_required
@permission_required("inventories.change_item", raise_exception=True)
@require_POST
def suspect_confirm(request, pk):
    """«Верно»: отложенные позиции описи входят в своды и итоги."""
    inv = get_object_or_404(Inventory.objects.select_related("source_file"), pk=pk)
    n = suspects.confirm(inv, request.user)
    messages.success(request, f"«{inv.source_file.name}»: подтверждено позиций — {n}; они входят в своды.")
    return _back(request)


@login_required
@permission_required("inventories.change_item", raise_exception=True)
@require_POST
def suspect_undo(request, pk):
    """Отменить «Верно»: позиции описи снова не в своде до проверки."""
    inv = get_object_or_404(Inventory.objects.select_related("source_file"), pk=pk)
    n = suspects.unconfirm(inv)
    messages.info(request, f"«{inv.source_file.name}»: подтверждение снято с позиций — {n}; они снова не в своде.")
    return _back(request)


def _export(s, request):
    """Подозрительные позиции — по строке на позицию, с причиной."""
    # Опись к каждой строке не тянется (у описи тяжёлые поля) — описи выборки одним запросом.
    items = list(s.items().select_related("suspect_ok_by").only(
        "inventory_id", "sheet", "row_no", "name", "inv_no", "recommended_code", "qty", "cost_initial",
        "cost_initial_raw", "cost_balance", "excluded", "suspect", "suspect_ok_at", "suspect_ok_by__username")
        .order_by("inventory__node__path", "inventory_id", "sheet", "row_no"))
    invs = Inventory.objects.select_related("node", "source_file").only(
        "node", "source_file__name", "source_file__path", "loaded_via", "loaded_from").in_bulk(
        {it.inventory_id for it in items})
    Node.fill_trails(i.node for i in invs.values())
    headers = ["Учреждение", "Файл описи", "Лист", "Строка", "Основное средство", "Инв. номер", "Рекомендуемый код",
               "Количество", "Первонач. стоимость, сом", "Балансовая стоимость, сом", "Почему подозрительна",
               "Подтверждена", "Кто подтвердил", "Путь к файлу"]

    def rows():
        for it in items:
            inv = invs[it.inventory_id]
            yield [inv.node.trail if inv.node else "", inv.source_file.name, it.sheet, it.row_no, it.name, it.inv_no,
                   it.recommended_code, it.qty, it.cost_initial, it.cost_balance, reasons(it),
                   timezone.localtime(it.suspect_ok_at).strftime("%d.%m.%Y %H:%M") if it.suspect_ok_at else "",
                   it.suspect_ok_by.get_username() if it.suspect_ok_by else "", inv.loaded_where]

    params = [("Параметр", "Значение"), ("Вид", "Подозрительные стоимости"), ("Описи", "актуальные"),
              ("Показаны", "подтверждённые («Верно»)" if s.ok else "не в своде — ждут «Верно»"),
              ("Причины", ", ".join(label for c, label in REASONS if c in s.cats)),
              ("Порог «дороже», сом", suspects.high()), ("Порог «дешевле», сом", suspects.low()),
              ("Позиций", len(items)), ("Кто выгрузил", request.user.get_username()),
              ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]
    return xlsx_file("Подозрительные стоимости", write_xlsx([
        ("Позиции", headers, rows, {"filter": True, "widths": {1: 40, 2: 40, 5: 40, 11: 40, 14: 60}}),
        ("Параметры", None, lambda: params, {"widths": {1: 26, 2: 60}})]))
