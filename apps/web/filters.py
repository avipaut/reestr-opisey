"""Отбор позиций — один для поиска, описи и выгрузки (одна реализация,
граф APCS: insight-duplicated-knowledge)."""

import datetime as dt
from decimal import Decimal, InvalidOperation

from django.db.models import Q
from django.utils import timezone

from apps.imports.reader import inv_key, norm
from apps.inventories.models import Inventory, Item, ObjectType, Status
from apps.orgs.models import Node

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
        self.qs = qs

    @property
    def active(self):
        return any([self.q, self.node, self.code, self.status, self.priority, self.otype, self.sheet,
                    self.cost_min is not None, self.cost_max is not None, self.marked, self.date])

    def params(self):
        p = [("На дату", self.date or "актуальные описи"),
             ("Учреждение (с подведами)", self.node.trail if self.node else "все")]
        for label, v in (("Текст", self.q), ("Код учёта", self.code), ("Статус", self.status),
                         ("Приоритет", self.priority), ("Тип объекта", self.otype), ("Лист", self.sheet),
                         ("Первонач. стоимость от", self.cost_min), ("до", self.cost_max),
                         ("Только с отметками", "да" if self.marked else "")):
            if v not in (None, ""):
                p.append((label, str(v)))
        return p

    def querystring(self, **drop):
        q = self.get.copy()
        for k in ("page", "export", *drop):
            q.pop(k, None)
        return q.urlencode()


def choices():
    return {
        "statuses": Status.objects.filter(is_active=True),
        "otypes": ObjectType.objects.all(),
        "sheets": SHEETS,
        "priorities": ["1", "2", "3", "4", "5", "0"],
    }
