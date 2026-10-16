"""Переставленные столбцы в уже загруженных описях: где стоимость ≤ 1, а в
«Количестве» стояла цена (позиция уже посчитана за 1 шт., исходное число —
в пометке), стоимость берётся из этого числа; балансовая — тоже, если она
равна первоначальной (0 — оставляется: может быть полный износ). Итоги
описей и своды пересчитываются. Решение пользователя 16.10.2026: «Правь в базе».
"""

import re
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Sum

from apps.inventories.models import Inventory, Item
from apps.stats.services import rebuild_stats

NUM = re.compile(r"в количестве (?:цена \()?(-?[\d.]+)")


class Command(BaseCommand):
    help = "Стоимость из «Количества» там, где столбцы переставлены (стоимость ≤ 1)"

    def handle(self, *args, **opts):
        n = 0
        inv_ids = set()
        with transaction.atomic():
            with connection.cursor() as c:
                c.execute("SET LOCAL statement_timeout = '900s'")
            qs = Item.objects.filter(qty_note__startswith="в количестве", cost_initial__lte=1).exclude(
                qty_note__contains="стоимость").only("id", "qty_note", "cost_initial", "cost_balance", "inventory_id")
            for it in qs.iterator(chunk_size=2000):
                m = NUM.search(it.qty_note)
                v = Decimal(m.group(1)) if m else None
                if v is None or v <= 1:
                    continue
                upd = {"cost_initial": v,
                       "qty_note": it.qty_note + f"; стоимость {it.cost_initial} заменена числом из «Количества»"}
                if it.cost_balance == it.cost_initial:
                    upd["cost_balance"] = v
                Item.objects.filter(pk=it.pk).update(**upd)
                inv_ids.add(it.inventory_id)
                n += 1
            for inv in Inventory.objects.filter(pk__in=inv_ids):
                agg = inv.items.aggregate(ci=Sum("cost_initial"), cb=Sum("cost_balance"))
                Inventory.objects.filter(pk=inv.pk).update(cost_initial=agg["ci"] or 0, cost_balance=agg["cb"] or 0)
                rebuild_stats(inv)
        self.stdout.write(f"стоимость восстановлена у позиций: {n}; пересчитано описей: {len(inv_ids)}")
