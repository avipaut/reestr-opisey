"""Отбор позиций — один для поиска, описи и выгрузки (одна реализация,
граф APCS: insight-duplicated-knowledge)."""

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.utils import timezone

from apps.imports.reader import inv_key, norm
from apps.inventories.models import Inventory, Item, ObjectType, Reconciliation, Status
from apps.orgs.models import Node
from apps.stats.flags import Flags
from apps.stats import services as stats

SUSPECT_CHOICES = [("any", "все подозрительные"), ("held", "не в своде — ждут «Верно»"), ("ok", "подтверждённые"),
                   ("high", "дороже порога"), ("low", "дешевле порога"), ("neg", "отрицательная"),
                   ("text", "буквы вместо числа")]

# Дубли описей (apps.imports.dedup): повтор строки другой описи группы — не в своде.
DUP_CHOICES = [("dup", "повторы строк других описей (не в своде)"), ("own", "свои строки (в своде)"),
               ("check", "сверить: тот же инв. номер с другой ценой или количеством")]

SHEETS = ["Опись", "Вынесено", "Ручная валидация"]


def _dec(v):
    try:
        return Decimal(v.replace(" ", "").replace(",", ".")) if v else None
    except InvalidOperation:
        return None


def when(request):
    """«На дату»: конец выбранных суток по местному времени; без даты — None (актуальные)."""
    raw = request.GET.get("date") or ""
    try:
        d = dt.date.fromisoformat(raw)
    except ValueError:
        return None, ""
    end = dt.datetime.combine(d + dt.timedelta(days=1), dt.time.min)
    return timezone.make_aware(end) - dt.timedelta(microseconds=1), raw


def inventories_at(at):
    """Описи, действовавшие в момент at; без даты — актуальные."""
    if at is None:
        return Inventory.objects.filter(is_current=True)
    return Inventory.objects.filter(state=Inventory.State.ACCEPTED, valid_from__lte=at).filter(
        Q(valid_to__isnull=True) | Q(valid_to__gt=at))


class ItemFilter:
    """Разбор параметров запроса → queryset позиций + подписи для «Параметров» выгрузки."""

    def __init__(self, request, base=None):
        g = request.GET
        self.get = g
        self.at, self.date = when(request)
        self.q = (g.get("q") or "").strip()
        self.node = Node.objects.filter(pk=g.get("node")).first() if (g.get("node") or "").isdigit() else None
        self.code = (g.get("code") or "").strip()
        self.status = Status.objects.filter(pk=g.get("status")).first() if (g.get("status") or "").isdigit() else None
        self.priority = g.get("priority") if (g.get("priority") or "") in ("0", "1", "2", "3", "4", "5") else ""
        self.otype = (ObjectType.objects.filter(pk=g.get("otype")).first()
                      if (g.get("otype") or "").isdigit() else None)
        self.sheet = g.get("sheet") if g.get("sheet") in SHEETS else ""
        self.cost_min, self.cost_max = _dec(g.get("cost_min")), _dec(g.get("cost_max"))
        self.marked = g.get("marked") == "1"
        # Подозрительная стоимость (вкладка «Подозрительные»): причина, «held» — не в своде, «ok» — подтверждена.
        self.suspect = g.get("suspect") if g.get("suspect") in ("any", "held", "ok", "text", "neg", "low", "high") else ""
        self.dup = g.get("dup") if g.get("dup") in dict(DUP_CHOICES) else ""
        # Флаги «учитывать»; отбор, который сам просит повторы или подозрительные, включает их.
        force = (["dup"] if self.dup == "dup" else []) + (["suspect"] if self.suspect and self.suspect != "ok" else [])
        self.flags = Flags.from_request(request, force=force)
        qs = base if base is not None else Item.objects.filter(inventory__in=inventories_at(self.at))
        if self.node:
            qs = qs.filter(inventory__node__path__startswith=self.node.path)
        if self.q:
            key = inv_key(self.q)
            cond = Q(search__contains=norm(self.q))
            if key:
                cond |= Q(inv_key=key)
            qs = qs.filter(cond)
        if self.code:
            qs = qs.filter(recommended_code=self.code)
        if self.status:
            qs = qs.filter(status=self.status)
        if self.priority:
            qs = qs.filter(priority=int(self.priority))
        if self.otype:
            qs = qs.filter(object_type=self.otype)
        if self.sheet:
            qs = qs.filter(sheet=self.sheet)
        if self.cost_min is not None:
            qs = qs.filter(cost_initial__gte=self.cost_min)
        if self.cost_max is not None:
            qs = qs.filter(cost_initial__lte=self.cost_max)
        if self.marked:
            qs = qs.exclude(user_mark="", name_fill="")
        if self.suspect == "held":
            qs = qs.filter(held=True)
        elif self.suspect == "ok":
            qs = qs.exclude(suspect="").filter(suspect_ok_at__isnull=False)
        elif self.suspect == "any":
            qs = qs.exclude(suspect="")
        elif self.suspect:
            qs = qs.filter(suspect=self.suspect)
        if self.dup == "dup":
            qs = qs.filter(dup_of__isnull=False)
        elif self.dup == "own":
            qs = qs.filter(dup_of__isnull=True)
        elif self.dup == "check":
            qs = qs.exclude(dup_check="")
        # all_qs — без флагов (для «не учтено»), qs — что учитывают флаги.
        self.all_qs = qs
        self.qs = qs.filter(self.flags.item_q())

    def totals(self):
        """Итоги выборки по флагам одним проходом: позиций, шт., стоимости и «не учтено»."""
        inc = self.flags.item_q()
        agg = self.all_qs.aggregate(
            rows=Count("id", filter=inc), qty=Sum("qty", filter=inc), cost=Sum("cost_initial", filter=inc),
            bal=Sum("cost_balance", filter=inc), all=Count("id"))
        agg["out"] = agg["all"] - agg["rows"]
        return agg

    @property
    def active(self):
        return any([self.q, self.node, self.code, self.status, self.priority, self.otype, self.sheet,
                    self.cost_min is not None, self.cost_max is not None, self.marked, self.suspect,
                    self.dup, self.date])

    def params(self):
        p = [("На дату", self.date or "актуальные описи"), *self.flags.params(), ("Что учтено", self.flags.summary()),
             ("Учреждение (с подведами)", self.node.trail if self.node else "все")]
        for label, v in (("Текст", self.q), ("Код учёта", self.code), ("Статус", self.status),
                         ("Приоритет", self.priority), ("Тип объекта", self.otype), ("Лист", self.sheet),
                         ("Первонач. стоимость от", self.cost_min), ("до", self.cost_max),
                         ("Только с отметками", "да" if self.marked else ""),
                         ("Подозрительная стоимость", dict(SUSPECT_CHOICES).get(self.suspect, "")),
                         ("Дубли описей", dict(DUP_CHOICES).get(self.dup, ""))):
            if v not in (None, ""):
                p.append((label, str(v)))
        return p

    def querystring(self, **drop):
        q = self.get.copy()
        for k in ("page", "export", *drop):
            q.pop(k, None)
        return q.urlencode()


def _int(v):
    v = (v or "").replace(" ", "")
    return int(v) if v.isdigit() else None


def _day(v):
    try:
        return dt.date.fromisoformat(v or "")
    except ValueError:
        return None


class InventoryFilter:
    """Отбор описей (экран «Описи» и его выгрузка): узел и текст — как прежде, плюс
    пометка «ПРОВЕРИТЬ», сверка с источником, вид источника, основа стоимостей,
    позиций, стоимость, дата приёма; порядок — щелчком по заголовку столбца."""

    # Позиций, шт., стоимость и «не учтено» — по своду и флагам (sums), порядок по ним — в коде.
    SUM_SORTS = {"rows": "rows", "qty": "qty", "cost": "ci", "out": "out"}
    SORTS = {"node": ("node__path", "id"), "rows": ("node__path",), "qty": ("node__path",),
             "cost": ("node__path",), "out": ("node__path",), "new": ("-valid_from", "node__path"),
             "versions": ("-versions", "node__path")}
    RECON = {"bad": "не сходится", "ok": "сходится", "none": "сверки нет"}

    def __init__(self, request):
        g = self.get = request.GET
        self.at, self.date = when(request)
        self.q = (g.get("q") or "").strip()
        self.node = Node.objects.filter(pk=g.get("node")).first() if (g.get("node") or "").isdigit() else None
        self.check = g.get("check") if g.get("check") in ("1", "0") else ""
        self.recon = g.get("recon") if g.get("recon") in self.RECON else ""
        self.kind = (g.get("kind") or "").strip()
        self.basis = (g.get("basis") or "").strip()
        self.rows_min, self.rows_max = _int(g.get("rows_min")), _int(g.get("rows_max"))
        self.cost_min, self.cost_max = _dec(g.get("cost_min")), _dec(g.get("cost_max"))
        self.acc_from, self.acc_to = _day(g.get("acc_from")), _day(g.get("acc_to"))
        self.sort = g.get("sort") if g.get("sort") in self.SORTS else "node"
        self.flags = Flags.from_request(request)

        qs = inventories_at(self.at).select_related("node", "source_file").annotate(
            versions=Count("node__inventories", filter=Q(node__inventories__state=Inventory.State.ACCEPTED)))
        if self.node:
            qs = qs.filter(node__path__startswith=self.node.path)
        if self.q:
            low = self.q.lower().replace("ё", "е")
            qs = qs.filter(Q(node__search__contains=low) | Q(node__code=self.q) | Q(source_file__name__contains=self.q)
                           | Q(org_text__contains=self.q))
        if self.check:
            qs = qs.filter(check_flag=self.check == "1")
        if self.recon:
            rec = Reconciliation.objects.filter(inventory=OuterRef("pk"))
            bad = rec.filter(matches__startswith="НЕТ")
            qs = (qs.filter(Exists(bad)) if self.recon == "bad" else
                  qs.filter(Exists(rec), ~Exists(bad)) if self.recon == "ok" else qs.filter(~Exists(rec)))
        if self.kind:
            qs = qs.filter(source_kind=self.kind)
        if self.basis:
            qs = qs.filter(cost_basis=self.basis)
        if self.acc_from:  # сутки — по местному времени
            qs = qs.filter(valid_from__gte=timezone.make_aware(dt.datetime.combine(self.acc_from, dt.time.min)))
        if self.acc_to:
            qs = qs.filter(valid_from__lt=timezone.make_aware(
                dt.datetime.combine(self.acc_to + dt.timedelta(days=1), dt.time.min)))
        self.qs = qs.order_by(*self.SORTS[self.sort])

    def ordered(self):
        """[(id описи, суммы)] выборки: суммы по своду на дату и флагам — одним запросом
        по всем описям выборки; отбор по позициям и стоимости и порядок по суммам — здесь."""
        ids = list(self.qs.values_list("pk", flat=True))
        sums = stats.inventory_sums(self.at or timezone.now(), ids, self.flags)
        zero = {"rows": 0, "qty": 0, "ci": 0, "cb": 0, "dep": 0, "out": 0, "all": 0}
        out = [(i, sums.get(i, zero)) for i in ids]
        for key, lo, hi in (("rows", self.rows_min, self.rows_max), ("ci", self.cost_min, self.cost_max)):
            if lo is not None:
                out = [x for x in out if x[1][key] >= lo]
            if hi is not None:
                out = [x for x in out if x[1][key] <= hi]
        if self.sort in self.SUM_SORTS:
            key = self.SUM_SORTS[self.sort]
            out.sort(key=lambda x: x[1][key], reverse=True)  # устойчиво: при равенстве — по узлу
        return out

    @property
    def more_active(self):
        """Заданы фильтры из «Ещё фильтры» — блок открыт сразу."""
        return any([self.check, self.recon, self.kind, self.basis, self.rows_min is not None,
                    self.rows_max is not None, self.cost_min is not None, self.cost_max is not None,
                    self.acc_from, self.acc_to])

    @property
    def active(self):
        return bool(self.q or self.node or self.date or self.more_active)

    def params(self):
        p = [("На дату", self.date or "актуальные описи"),
             ("Учреждение (с подведами)", self.node.trail if self.node else "все"), *self.flags.params(),
             ("Что учтено", self.flags.summary())]
        for label, v in (("Текст", self.q), ("Пометка «ПРОВЕРИТЬ»", {"1": "есть", "0": "нет"}.get(self.check, "")),
                         ("Сверка с источником", self.RECON.get(self.recon, "")), ("Вид источника", self.kind),
                         ("Основа стоимостей", self.basis), ("Позиций от", self.rows_min), ("Позиций до", self.rows_max),
                         ("Первонач. стоимость от", self.cost_min), ("до", self.cost_max),
                         ("Принята с", self.acc_from and self.acc_from.strftime("%d.%m.%Y")),
                         ("Принята по", self.acc_to and self.acc_to.strftime("%d.%m.%Y"))):
            if v not in (None, ""):
                p.append((label, str(v)))
        return p

    def sort_url(self, key):
        q = self.get.copy()
        for k in ("page", "export", "sort"):
            q.pop(k, None)
        if key != "node":
            q["sort"] = key
        return "?" + q.urlencode()


def inventory_choices():
    """Значения для списков фильтра описей — из актуальных описей, частые выше."""
    cur = Inventory.objects.filter(is_current=True)
    return {
        "kinds": [r["source_kind"] for r in cur.exclude(source_kind="").values("source_kind")
                  .annotate(n=Count("id")).order_by("-n", "source_kind")],
        "bases": [r["cost_basis"] for r in cur.exclude(cost_basis="").values("cost_basis")
                  .annotate(n=Count("id")).order_by("-n", "cost_basis")],
    }


def choices():
    return {
        "statuses": Status.objects.filter(is_active=True), "suspect_choices": SUSPECT_CHOICES, "dup_choices": DUP_CHOICES,
        "otypes": ObjectType.objects.all(),
        "sheets": SHEETS,
        "priorities": ["1", "2", "3", "4", "5", "0"],
    }
