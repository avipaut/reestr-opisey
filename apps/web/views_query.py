"""Конструктор запросов (решение пользователя 29.10.2026: «в идеале добавить страницу —
конструктор запросов»; состав — группировка, отборы, выгрузка xlsx, сохранённые запросы).

Считает по позициям описей, действовавших на дату: группировка до трёх уровней, отборы —
те же, что у поиска позиций (ItemFilter), плюс начало кода и год описи; флаги «учитывать» —
как на остальных страницах. Величины — позиций, шт., стоимости и «не учтено».
"""

from types import SimpleNamespace

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.db.models import CharField, Count, F, Func, Q, Sum, Value
from django.db.models.functions import Concat
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.inventories.models import Inventory
from apps.orgs.models import Node
from apps.stats.flags import FLAGS
from apps.stats.models import SavedQuery

from apps.imports.reader import inv_key, norm

from .filters import ItemFilter, choices
from .xlsx import items_xlsx_response, xlsx_response

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


NONE = "~"  # значение разреза «пусто» в адресе сводки строки


# Операции условий (пользователь 29.10.2026: «добавь возможность запросов с условиями по типу
# Исключает»; «Приоритет задаем числом. Для поиска по условиям»): у условия — «равно» или
# «исключает» (?op_status=not), приоритет — числом со сравнением (?op_priority=le).
NEGATABLE = {"q", "node", "code", "status", "otype", "sheet", "year"}
PRIO_OPS = {"eq": "=", "ne": "≠", "le": "≤", "ge": "≥"}
# условие → параметры, которые ItemFilter применил бы как «равно»
_POSITIVE = {"q": ["q"], "node": ["node"], "status": ["status"], "otype": ["otype"], "sheet": ["sheet"]}
_LABEL = {"q": "Текст", "node": "Учреждение (с подведами)", "status": "Статус", "otype": "Тип объекта", "sheet": "Лист"}


def base_query(request):
    """Позиции запроса конструктора без флагов (f.flags — что из них учитывать). f — отбор для
    показа (все значения условий), позиции отбираются с учётом «исключает» и приоритета."""
    G = request.GET
    f = ItemFilter(request)
    neg = {k for k in NEGATABLE if G.get(f"op_{k}") == "not"}
    g = G.copy()
    for k in neg & _POSITIVE.keys():
        for x in _POSITIVE[k]:
            g.pop(x, None)
    g.pop("priority", None)
    qs = ItemFilter(SimpleNamespace(GET=g)).all_qs
    if "q" in neg and f.q:
        key = inv_key(f.q)
        cond = Q(search__contains=norm(f.q)) | (Q(inv_key=key) if key else Q(pk__in=[]))
        qs = qs.exclude(cond)
    if "node" in neg and f.node:
        qs = qs.exclude(inventory__node__path__startswith=f.node.path)
    if "status" in neg and f.status:
        qs = qs.exclude(status=f.status)
    if "otype" in neg and f.otype:
        qs = qs.exclude(object_type=f.otype)
    if "sheet" in neg and f.sheet:
        qs = qs.exclude(sheet=f.sheet)
    prefix = (G.get("code_prefix") or "").strip()
    if prefix:
        qs = qs.exclude(recommended_code__startswith=prefix) if "code" in neg else qs.filter(recommended_code__startswith=prefix)
    year = G.get("year") or ""
    if year.isdigit():
        qs = qs.exclude(inventory__year=int(year)) if "year" in neg else qs.filter(inventory__year=int(year))
    prio = (G.get("priority") or "").strip()
    op = G.get("op_priority") if G.get("op_priority") in PRIO_OPS else "eq"
    f.prio = None
    if prio.lstrip("-").isdigit():
        n = int(prio)
        qs = {"eq": lambda: qs.filter(priority=n), "ne": lambda: qs.exclude(priority=n),
              "le": lambda: qs.filter(priority__lte=n), "ge": lambda: qs.filter(priority__gte=n)}[op]()
        f.prio = (op, n)
    f.neg = neg
    return f, qs, prefix, year


def row_filter(qs, request):
    """Позиции одной строки результата: значения разрезов из адреса (?d_status=Резерв),
    тем же выражением, что и группировка, — числа сводки сходятся со строкой таблицы."""
    picked = []
    order = _groups(request) + [g for g in DIMS if g not in _groups(request)]  # в порядке разрезов запроса
    for g in order:
        expr = DIMS[g][1]
        if f"d_{g}" not in request.GET:
            continue
        v = request.GET.get(f"d_{g}")
        qs = qs.annotate(**{f"r_{g}": expr})
        if v == NONE:
            qs = qs.filter(Q(**{f"r_{g}__isnull": True}) | Q(**{f"r_{g}": ""})) if g not in ("priority", "year") \
                else qs.filter(**{f"r_{g}__isnull": True})
        else:
            qs = qs.filter(**{f"r_{g}": int(v) if g in ("priority", "year") and v.lstrip("-").isdigit() else v})
        picked.append((g, v))
    return qs, picked


def run_query(request):
    f, qs, prefix, year = base_query(request)
    groups = _groups(request)
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
        dims, raw = [], []
        for g in groups:
            v = r[f"d_{g}"]
            dims.append(labels.get(g, {}).get(v, v) if v not in (None, "") else "—")
            raw.append((f"d_{g}", NONE if v in (None, "") else str(v)))
        table.append({"dims": dims, "raw": raw, **vals})
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
         ("code", "Рекомендуемый код", ["code_prefix"]), ("status", "Статус", ["status"]),
         ("priority", "Приоритет", ["priority"]), ("otype", "Тип объекта", ["otype"]), ("sheet", "Лист", ["sheet"]),
         ("year", "Год описи", ["year"]), ("cost", "Первонач. стоимость", ["cost_min", "cost_max"]),
         ("date", "На дату", ["date"])]


def _measures(request):
    picked = [m for m in request.GET.getlist("m") if m in dict(MEASURES)]
    return [(k, label) for k, label in MEASURES if not picked or k in picked]


def _params(request, f, groups, prefix, year):
    neg = getattr(f, "neg", set())
    negl = {_LABEL[k] for k in neg if k in _LABEL}
    own = [(f"{label} — исключает" if label in negl else label, v) for label, v in f.params() if label != "Приоритет"]
    p = [("Вид", "Конструктор запросов"), ("Разрезы", " › ".join(DIMS[g][0] for g in groups) or "без разрезов"), *own]
    if prefix:
        p.append(("Рекомендуемый код " + ("не начинается с" if "code" in neg else "начинается с"), prefix))
    if year:
        p.append(("Год описи" + (" — исключает" if "year" in neg else ""), year))
    if getattr(f, "prio", None):
        p.append(("Приоритет", f"{PRIO_OPS[f.prio[0]]} {f.prio[1]}"))
    return p + [("Кто выгрузил", request.user.get_username()), ("Когда", timezone.localtime().strftime("%d.%m.%Y %H:%M"))]


def _conditions_text(f, prefix, year):
    """Условия словами для шапки результата; «исключает» — «кроме …»."""
    neg = getattr(f, "neg", set())

    def say(key, text):
        out.append(f"кроме: {text}" if key in neg else text)

    out = []
    if f.q:
        out.append(f"без «{f.q}»" if "q" in neg else f"текст «{f.q}»")
    if f.node:
        say("node", f.node.trail)
    if prefix:
        say("code", f"код {prefix}…")
    if f.status:
        say("status", f.status.name)
    if getattr(f, "prio", None):
        out.append(f"приоритет {PRIO_OPS[f.prio[0]]} {f.prio[1]}")
    if f.otype:
        say("otype", f.otype.name)
    if f.sheet:
        say("sheet", f"лист «{f.sheet}»")
    if year:
        say("year", f"опись {year} года")
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
           "presets": PRESETS, "conds": CONDS, "active": active, "prio_ops": PRIO_OPS,
           "neg": {k for k in NEGATABLE if g.get(f"op_{k}") == "not"}, "op_priority": g.get("op_priority") or "eq",
           "prio_value": (g.get("priority") or "").strip(),
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
    for t in table[:SHOW]:
        rq = q.copy()
        for k, v in t["raw"]:
            rq[k] = v
        t["href"] = rq.urlencode()
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


def _prio(v):
    """Приоритет подписью: «П1»; пусто — «без приоритета»."""
    return f"П{v}" if v is not None else "без приоритета"


def _pkey(v):
    return str(v) if v is not None else "none"


def _dim_title(g, v):
    """Значение разреза словами для заголовка сводки."""
    if v == NONE:
        return "—"
    if g in ("node", "org", "sub"):
        return _labels(g, {v}).get(v, v)
    return v


@login_required
def query_row(request):
    """Сводка строки результата (пользователь 29.10.2026: «нажимать на результаты… и что бы в
    отдельной странице/окне открывалась сводка что это такое, где и откуда. Ну и само собой
    переход к источнику»): что — разрезы, условия, итоги и состав; где и откуда — описи
    с узлом, файлом и путём; позиции — со ссылками на карточку и опись, выгрузка."""
    f, qs, prefix, year = base_query(request)
    qs, picked = row_filter(qs, request)
    inc = f.flags.item_q()
    counted = qs.filter(inc)
    if request.GET.get("export") == "xlsx":
        invs = list(Inventory.objects.filter(pk__in=counted.values("inventory_id")).select_related("node").only("id", "node"))
        Node.fill_trails(i.node for i in invs)
        heads = [(DIMS[g][0], _dim_title(g, v)) for g, v in picked]
        return items_xlsx_response("Строка запроса", counted.select_related("status", "object_type").order_by(
            "inventory__node__path", "sheet", "row_no"), heads + _params(request, f, [], prefix, year)[2:], request,
            trails={i.pk: i.node.trail if i.node else "" for i in invs})
    with transaction.atomic(), connection.cursor() as c:
        c.execute("SET LOCAL statement_timeout = '180s'")
        tot = qs.aggregate(rows=Count("id", filter=inc), qty=Sum("qty", filter=inc), ci=Sum("cost_initial", filter=inc),
                           cb=Sum("cost_balance", filter=inc), all=Count("id"),
                           invs=Count("inventory", distinct=True, filter=inc),
                           nodes=Count("inventory__node", distinct=True, filter=inc))
        tot["out"] = tot["all"] - (tot["rows"] or 0)
        by_inv = list(qs.values("inventory_id").annotate(rows=Count("id", filter=inc), qty=Sum("qty", filter=inc),
                                                         ci=Sum("cost_initial", filter=inc), all=Count("id"))
                      .order_by("-rows", "-all")[:200])

        def part(field, n=8):
            return list(counted.values(k=F(field)).annotate(rows=Count("id"), qty=Sum("qty"), ci=Sum("cost_initial"))
                        .order_by("-rows")[:n])

        # Приоритеты (пользователь 29.10.2026: «добавь в отображение приоритеты. обязательно»):
        # по строке целиком и по каждой её описи.
        prio = list(counted.values("priority").annotate(rows=Count("id"), qty=Sum("qty"), ci=Sum("cost_initial"))
                    .order_by("priority"))
        prio_by_inv = {}
        for r in counted.filter(inventory_id__in=[x["inventory_id"] for x in by_inv]).values(
                "inventory_id", "priority").annotate(n=Count("id")).order_by("priority"):
            short = f"П{r['priority']}" if r["priority"] is not None else "без П"
            prio_by_inv.setdefault(r["inventory_id"], []).append((short, r["n"], _pkey(r["priority"])))
        # Состав строки — по полям, которыми она ещё не разрезана.
        used = {g for g, _ in picked}
        parts = [(t, part(fld)) for g, t, fld in (("status", "Статус", "status__name"),
                                                  ("code", "Рекомендуемый код", "recommended_code"),
                                                  ("otype", "Тип объекта", "object_type__name")) if g not in used]
    invs = Inventory.objects.select_related("node", "source_file", "loaded_by").defer(
        "summary_lines", "unknown_columns", "proposed_path").in_bulk([r["inventory_id"] for r in by_inv])
    Node.fill_trails(i.node for i in invs.values())
    sources = [{"inv": invs[r["inventory_id"]], **r, "out": r["all"] - r["rows"], "prio": prio_by_inv.get(r["inventory_id"], [])}
               for r in by_inv if r["inventory_id"] in invs]
    total_rows = tot["rows"] or 0
    prio = [{"label": _prio(r["priority"]), "key": _pkey(r["priority"]), "rows": r["rows"], "qty": r["qty"], "ci": r["ci"],
             "share": r["rows"] * 100 / total_rows if total_rows else 0} for r in prio]
    pager = Paginator(counted.select_related("inventory__node", "status", "object_type")
                      .order_by("inventory__node__path", "sheet", "row_no"), 50)
    pager.count = tot["rows"] or 0  # уже посчитано итогами — второй проход по позициям не нужен
    page = pager.get_page(request.GET.get("page"))
    q = request.GET.copy()
    for k in ("page", "export"):
        q.pop(k, None)
    back = q.copy()
    for k in list(back):
        if k.startswith("d_"):
            back.pop(k)
    with_all = q.copy()
    with_all.setlist("inc", [k for k, _, _ in FLAGS])
    title = " · ".join(f"{DIMS[g][0]}: {_dim_title(g, v)}" for g, v in picked) or "Все позиции выборки"
    return render(request, "web/query_row.html", {
        "title": title, "picked": [(DIMS[g][0], _dim_title(g, v)) for g, v in picked], "tot": tot,
        "sources": sources, "sources_more": max(0, (tot["invs"] or 0) - len(sources)), "parts": parts, "page": page,
        "prio": prio if "priority" not in {g for g, _ in picked} else [],
        "f": f, "flags": f.flags, "cond_text": _conditions_text(f, prefix, year), "here": q.urlencode(),
        "back": back.urlencode(), "with_all": with_all.urlencode(), "nav": "query"})
