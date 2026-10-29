"""Пересчитать своды всех версий описей с прежними сроками действия — после смены
устройства свода (29.10.2026: в своде все позиции с видом строки для флагов «учитывать»).

    .venv\Scripts\python manage.py rebuild_stats
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.inventories.models import Inventory
from apps.stats.services import rebuild_stats


class Command(BaseCommand):
    help = "Пересчитать своды всех версий описей (сроки действия сохраняются)"

    def handle(self, *args, **opts):
        ids = list(Inventory.objects.filter(state=Inventory.State.ACCEPTED).order_by("pk").values_list("pk", flat=True))
        for n, pk in enumerate(ids, 1):
            with transaction.atomic():
                rebuild_stats(Inventory.objects.get(pk=pk))
            if n % 500 == 0:
                self.stdout.write(f"  описей {n} из {len(ids)}")
        self.stdout.write(f"готово: {len(ids)}")
