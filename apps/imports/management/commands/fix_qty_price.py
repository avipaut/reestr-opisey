"""Позиции, где в «Количестве» не штуки, а сумма (равно первонач. или
балансовой стоимости либо больше 1000), — за 1 шт.; исходное число — в
пометке; своды затронутых описей пересчитываются. То же правило — у разбора
новых книг (reader.py, QTY_MAX)."""

from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import F, Q
from django.db.models.functions import Floor

from apps.imports.reader import QTY_MAX
from apps.inventories.models import Inventory, Item
from apps.stats.services import fill_totals, rebuild_stats


class Command(BaseCommand):
    help = "Сумма в «Количестве» → 1 шт. с пометкой; пересчёт сводов"

    def handle(self, *args, **opts):
        bad = Item.objects.annotate(f=Floor("qty")).filter(qty_note="").filter(
            ~Q(qty__in=[0, 1]),
            Q(qty=F("cost_initial")) | Q(qty=F("cost_balance")) | Q(qty__gt=QTY_MAX)
            | (Q(cost_initial__lte=1) & ~Q(qty=F("f"))))
        n = 0
        with transaction.atomic():
            with connection.cursor() as c:  # обход всех позиций — дольше обычного запроса
                c.execute("SET LOCAL statement_timeout = '900s'")
            inv_ids = list(bad.values_list("inventory_id", flat=True).distinct())
            for it in bad.only("id", "qty").iterator(chunk_size=2000):
                Item.objects.filter(pk=it.pk).update(qty=1, qty_note=f"в количестве {it.qty} — похоже на сумму, за 1 шт.")
                n += 1
            for inv in Inventory.objects.filter(pk__in=inv_ids):
                rebuild_stats(inv)
                fill_totals(inv)
        self.stdout.write(f"исправлено позиций: {n}; пересчитано описей: {len(inv_ids)}")
