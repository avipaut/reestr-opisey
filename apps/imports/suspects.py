"""Подозрительная первоначальная стоимость (решение пользователя 28.10.2026:
«сделай так чтобы они не учитывались в основной статистике пока на вкладке
подозрительные я не нажму кнопку верно»).

Позиция с подозрительной стоимостью отмечается при загрузке (suspect — причина)
и откладывается (held): в своды, итоги и количество не входит. «Верно» на вкладке
«Подозрительные» подтверждает отложенные позиции описи — они входят в свод, в том
числе «на прошлые даты» (это исправление, а не новое событие). Подтверждение
переходит в новую версию той же книги на те же строки (по хешу строки).

Повод — 27.10.2026 в итогах 11 трлн сом: в «!ПРОВЕРИТЬ — опись ФУГИ» модуль
нормализации принял инвентарный номер за стоимость (311 231 900 008)."""

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.inventories.models import Inventory, Item
from apps.stats import services as stats

# Порядок — приоритет причины: буквы, затем знак, затем пороги.
REASONS = [("text", "буквы вместо числа"), ("neg", "отрицательная"), ("low", "дешевле порога, но больше 0"),
           ("high", "дороже порога")]
LABELS = dict(REASONS)


def high():
    return Decimal(str(settings.SUSPECT_HIGH))


def low():
    return Decimal(str(settings.SUSPECT_LOW))


def conditions():
    """{причина: условие на позицию} по нынешним порогам."""
    return {"text": ~Q(cost_initial_raw=""), "neg": Q(cost_initial__lt=0),
            "low": Q(cost_initial__gt=0, cost_initial__lt=low()), "high": Q(cost_initial__gte=high())}


def reason(cost, raw=""):
    """Причина для разобранной позиции (cost — строка из разбора или Decimal)."""
    if raw:
        return "text"
    if cost in (None, ""):
        return ""
    try:
        v = Decimal(str(cost))
    except InvalidOperation:
        return ""
    if v < 0:
        return "neg"
    if 0 < v < low():
        return "low"
    if v >= high():
        return "high"
    return ""


def looks_like_code(v):
    """Стоимость похожа на инвентарный номер — код счёта «31…» и порядковый номер."""
    if v is None or v != v.to_integral_value():
        return False
    s = str(int(v))
    return len(s) >= 11 and s.startswith("31")


def _recount(inv_ids):
    """Итоги и свод описей заново — с прежними сроками действия (исправление данных)."""
    for inv in Inventory.objects.filter(pk__in=list(inv_ids)):
        stats.fill_totals(inv)
        stats.rebuild_stats(inv)


@transaction.atomic
def confirm(inv, user=None):
    """«Верно»: отложенные позиции описи входят в свод. Возвращает их число."""
    n = Item.objects.filter(inventory=inv, held=True).update(held=False, suspect_ok_at=timezone.now(),
                                                             suspect_ok_by=user)
    if n:
        _recount([inv.pk])
    return n


@transaction.atomic
def unconfirm(inv):
    """Отменить «Верно»: подтверждённые позиции описи снова отложены."""
    n = (Item.objects.filter(inventory=inv, suspect_ok_at__isnull=False).exclude(suspect="")
         .update(held=True, suspect_ok_at=None, suspect_ok_by=None))
    if n:
        _recount([inv.pk])
    return n


def recheck():
    """Отметить подозрительные позиции всех описей по нынешним порогам (после их смены и
    при первом запуске); подтверждённые остаются подтверждёнными. Возвращает
    (отложено позиций, пересчитано описей)."""
    with transaction.atomic():
        before = set(Item.objects.filter(held=True).values_list("inventory_id", flat=True).distinct())
        Item.objects.exclude(suspect="").update(suspect="")
        for code, q in conditions().items():
            Item.objects.filter(q, suspect="").update(suspect=code)
        Item.objects.filter(suspect="", held=True).update(held=False)
        Item.objects.exclude(suspect="").filter(suspect_ok_at__isnull=True).update(held=True)
        held = Item.objects.filter(held=True)
        after = set(held.values_list("inventory_id", flat=True).distinct())
        _recount(before | after)
        return held.count(), len(before | after)
