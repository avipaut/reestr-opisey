"""Числа на экране: разряды пробелом, дробь — запятой; ноль — тире-заглушка
не ставится, пусто — пусто."""

from decimal import Decimal

from django import template

register = template.Library()


@register.filter
def num(v, places=None):
    if v is None or v == "":
        return ""
    v = Decimal(v)
    if places is None:
        places = 0 if v == v.to_integral() else 2
    s = f"{v:,.{int(places)}f}".replace(",", " ").replace(".", ",")
    return s


@register.filter
def signed(v):
    if v is None:
        return ""
    v = Decimal(v)
    if v == 0:
        return "0"
    return ("+" if v > 0 else "−") + num(abs(v))


@register.filter
def field_label(f):
    return {"name": "наименование", "recommended_code": "код", "status__name": "статус",
            "qty": "количество", "cost_initial": "первонач. стоимость"}.get(f, f)


@register.filter
def get(d, key):
    """Значение словаря по ключу из переменной: {{ row|get:k }}."""
    return d.get(key) if hasattr(d, "get") else None
