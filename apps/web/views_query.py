"""Конструктор запросов (решение пользователя 29.10.2026: «в идеале добавить страницу —
конструктор запросов»; состав — группировка, отборы, выгрузка xlsx, сохранённые запросы).

Считает по позициям описей, действовавших на дату: группировка до трёх уровней, отборы —
те же, что у поиска позиций (ItemFilter), плюс начало кода и год описи; флаги «учитывать» —
как на остальных страницах. Величины — позиций, шт., стоимости и «не учтено».
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection, transaction
from django.db.models import CharField, Count, F, Func, Sum, Value
from django.db.models.functions import Concat
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.inventories.models import Inventory
from apps.orgs.models import Node
from apps.stats.models import SavedQuery

from .filters import ItemFilter, choices
from .xlsx import xlsx_response

def _part(n):
    """n-я часть пути узла «/126/8/…» — код уровня дерева."""
    return Func(F("inventory__node__path"), Value("/"), Value(n), function="split_part", output_field=CharField())


# ключ → (подпись, выражение)
DIMS = {
    "code": ("Рекомендуемый код", F("recommended_code")),
    "account": ("Код учёта", F("account_code")),
    "status": ("Статус", F("status__name")),
    "org": ("Ведомство (код)", _part(2)),
    "sub": ("Подвед", Concat(Value("/"), _part(2), Value("/"), _part(3), Value("/"), output_field=CharField())),
    "node": ("Узел описи", F("inventory__node__path")),
    "dept": ("Ведомство (как в описи)", F("department_text")),
    "otype": ("Тип объекта", F("object_type__name")),
    "priority": ("Приоритет", F("priority")),
    "sheet": ("Лист", F("sheet")),
    "year": ("Год описи", F("inventory__year")),
}
MEASURES = [("rows", "Позиций"), ("qty", "Количество, шт."), ("ci", "Первонач. стоимость, сом"),
            ("dep", "Накоп. износ, сом"), ("cb", "Балансовая стоимость, сом"), ("out", "Не учтено, позиций")]
# Короткие подписи столбцов на экране (полные — в подсказке заголовка и в выгрузке).
SHORT = {"rows": "Позиций", "qty": "Шт.", "ci": "Первонач., сом", "dep": "Износ, сом", "cb": "Балансовая, сом",
         "out": "Не учтено"}
SHOW = 1000  # строк на странице; выгрузка — все


def _groups(request):
    out = []
    for k in ("g1", "g2", "g3"):
        v = request.GET.get(k) or ""
        if v in DIMS and v not in out:
            out.append(v)
    return out


def _labels(dim, values):
    """Подписи значений: узлы — цепочкой, учреждения — по коду верхнего уровня."""
    if dim == "node":
        return {n.path: n.trail for n in Node.objects.filter(path__in=[v for v in values if v])}
    if dim == "sub":
        nodes = list(Node.objects.filter(path__in=[v for v in values if v and v != "///"]))
        Node.fill_trails(nodes)
        return {n.path: n.trail for n in nodes} | {"///": "—"}
    if dim == "org":
        nodes = Node.objects.filter(path__in=[f"/{v}/" for v in values if v])
        return {n.path.strip("/"): n.label for n in nodes}
    return {}


def run_query(request):
    f = ItemFilter(request)
    groups = _groups(request)
    qs = f.all_qs
    prefix = (request.GET.get("code_prefix") or "").strip()
    if prefix:
        qs = qs.filter(recommended_code__startswith=prefix)
    year = request.GET.get("year") or ""
    if year.isdigit():
        qs = qs.filter(inventory__year=int(year))
    inc = f.flags.item_q()
    sums = dict(x_rows=Count("id", filter=inc), x_qty=Sum("qty", filter=inc), x_ci=Sum("cost_initial", filter=inc),
                x_dep=Sum("depreciation", filter=inc), x_cb=Sum("cost_balance", filter=inc), x_all=Count("id"))
    # Группировка по всей базе — несколько секунд; предел запроса поднят только здесь.
    with transaction.atomic(), connection.cursor() as c:
        c.execute("SET LOCAL statement_timeout = '180s'")
        if groups:
            keys = [f"d_{g}" for g in groups]
            rows = list(qs.annotate(**{f"d_{g}": DIMS[g][1] for g in groups}).values(*keys).annotate(**sums)
                        .order_by(*keys))
        else:
            rows = [qs.aggregate(**sums)]
    labels = {g: _labels(g, {r[f"d_{g}"] for r in rows}) for g in groups if g in ("node", "org", "sub")}
    table = []
    for r in rows:
        vals = {k: r[f"x_{k}"] or 0 for k in ("rows", "qty", "ci", "dep", "cb")}
        vals["out"] = (r["x_all"] or 0) - vals["rows"]
        if not vals["rows"] and not vals["out"]:
            continue
        dims = []
        for g in groups:
            v = r[f"d_{g}"]
            dims.append(labels.get(g, {}).get(v, v) if v not in (None, "") else "—")
        table.append({"dims": dims, **vals})
    total = {k: sum(t[k] for t in table) for k, _ in MEASURES}
    return f, groups, table, total, prefix, year


# Готовые запросы — с них удобно начать (по образцу заготовок отбора APCS).
PRESETS = [
    ("Позиции по статусам", "g1=status"),
    ("Коды × статус", "g1=code&g2=status"),
    ("Подведы × статус", "g1=sub&g2=status"),
    ("По годам описей", "g1=year&g2=status"),
    ("Типы объектов", "g1=otype&g2=status"),
    ("Дорогие позиции по подведам", "g1=sub&cost_min=1000000"),
]
# Условия: ключ → (подпись, параметры адреса). Все условия выполняются вместе («и»).
CONDS = [("q", "Наименование, инв. номер или модель", ["q"]), ("node", "Учреждение (с подведами)", ["node"]),
         ("code", "Рекомендуемый код начинается с", ["code_prefix"]), ("status", "Статус", ["status"]),
         ("priority", "Приоритет", ["priority"]), ("otype", "Тип объекта", ["otype"]), ("sheet", "Лист", ["sheet"]),
         ("year", "Год описи", ["year"]), ("cost", "Первонач. стоимость", ["cost_min", "cost_max"]),
         ("date", "На дату", ["date"])]


def _measures(request):
    picked = [m for m in request.GET.getlist("m") if m in dict(MEASURES)]
    return [(k, label) for k, label in MEASURES if not picked or k in picked]


def _params(request, f, groups, prefix, year):
    p = [("Вид", "Конструктор запросов"), ("Разрезы", " › ".join(DIMS[g][0] for g in groups) or "без разрезов"),
         *f.params()]
    if prefix:
        p.append(("Рекомендуемый код начинается с", prefix))
    if year:
        p.append(("Год описи", year))
    return p + [("Кто выгрузил", request.user.get_username()), ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]


def _conditions_text(f, prefix, year):
    """Условия словами для шапки результата."""
    out = []
    if f.q:
        out.append(f"текст «{f.q}»")
    if f.node:
        out.append(f.node.trail)
    if prefix:
        out.append(f"код {prefix}…")
    if f.status:
        out.append(f.status.name)
    if f.priority:
        out.append(f"приоритет {f.priority}")
    if f.otype:
        out.append(f.otype.name)
    if f.sheet:
        out.append(f"лист «{f.sheet}»")
    if year:
        out.append(f"опись {year} года")
    if f.cost_min is not None or f.cost_max is not None:
        lo = f"от {f.cost_min:,.0f}".replace(",", " ") if f.cost_min is not None else ""
        hi = f"до {f.cost_max:,.0f}".replace(",", " ") if f.cost_max is not None else ""
        out.append(f"стоимость {' '.join(x for x in (lo, hi) if x)} сом")
    out.append(f"на {f.date}" if f.date else "актуальные описи")
    return out


@login_required
def query_page(request):
    if not request.GET:
        # Пустого экрана нет: без условий — сразу свод по статусам.
        return redirect(f"{request.path}?g1=status")
    asked = True
    g = request.GET
    active = {k for k, _, keys in CONDS if any((g.get(x) or "").strip() for x in keys)}
    ctx = {"dims": [(k, v[0]) for k, v in DIMS.items()], "all_measures": MEASURES, "saved": SavedQuery.objects.all(),
           "presets": PRESETS, "conds": CONDS, "active": active,
           "years": sorted({y for y in Inventory.objects.filter(is_current=True).exclude(year__isnull=True)
                            .values_list("year", flat=True)}, reverse=True), "nav": "query", **choices()}
    if not asked:
        f = ItemFilter(request)
        return render(request, "web/query.html", {**ctx, "f": f, "flags": f.flags, "groups": [],
                                                  "measures": MEASURES, "picked_m": {k for k, _ in MEASURES}})
    f, groups, table, total, prefix, year = run_query(request)
    measures = _measures(request)
    if request.GET.get("export") == "xlsx":
        heads = [DIMS[x][0] for x in groups] + [m for _, m in measures]
        rows = [[*t["dims"], *[t[k] for k, _ in measures]] for t in table]
        tot = ["Итого", *[""] * (len(groups) - 1), *[total[k] for k, _ in measures]] if groups else None
        if not groups:
            heads, rows = ["Выборка", *heads], [["Все позиции", *r] for r in rows]
        return xlsx_response("Запрос", heads, rows, tot, _params(request, f, groups, prefix, year))
    q = request.GET.copy()
    q.pop("export", None)
    # Повтор значения разреза в соседней строке не печатается — таблица читается группами.
    prev = [None] * len(groups)
    for t in table[:SHOW]:
        t["show"] = []
        same = True
        for n, d in enumerate(t["dims"]):
            same = same and prev[n] == d
            t["show"].append("" if same and n < len(groups) - 1 else d)
        prev = t["dims"]
    title = ("Позиции: " + " › ".join(DIMS[x][0].lower() for x in groups)) if groups else "Все позиции выборки"
    return render(request, "web/query.html", {
        **ctx, "f": f, "flags": f.flags, "groups": groups, "table": table[:SHOW],
        "more": max(0, len(table) - SHOW), "count": len(table), "total": total, "code_prefix": prefix, "year": year,
        "here": q.urlencode(), "group_heads": [DIMS[x][0] for x in groups], "asked": True,
        "measures": measures, "short": SHORT, "picked_m": {k for k, _ in measures}, "title": title,
        "cond_text": _conditions_text(f, prefix, year)})


@login_required
@require_POST
def query_save(request):
    name = (request.POST.get("name") or "").strip()
    query = request.POST.get("query") or ""
    if not name:
        messages.error(request, "Не сохранено: укажите название запроса")
    else:
        obj, made = SavedQuery.objects.update_or_create(name=name, defaults={"query": query, "created_by": request.user})
        messages.success(request, f"Запрос «{name}» {'сохранён' if made else 'обновлён'}")
    return redirect(f"{request.path.rsplit('save/', 1)[0]}?{query}")


@login_required
@require_POST
def query_delete(request, pk):
    obj = get_object_or_404(SavedQuery, pk=pk)
    if obj.created_by_id != request.user.pk and not request.user.is_staff:
        messages.error(request, "Удалить запрос может тот, кто его сохранил, или администратор")
    else:
        obj.delete()
        messages.success(request, f"Запрос «{obj.name}» удалён")
    return redirect("query")
