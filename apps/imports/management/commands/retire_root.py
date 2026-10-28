"""Папку больше не читаем (реестр переключили на другую): её описи снимаются
с актуальных, история «на дату» остаётся. Ничего не удаляется.

21.10.2026: реестр читал устаревшую копию «10. Нормализованные описи\\Посл»,
а модуль нормализации пишет в «12. Описи»."""

import os

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.inventories.models import Inventory, SourceFile
from apps.imports import dedup
from apps.imports.services import retire


class Command(BaseCommand):
    help = "Снять с актуальных описи книг из папки (корня обхода), которую реестр больше не читает"

    def add_arguments(self, parser):
        parser.add_argument("root", help="корень обхода, как в журнале обходов")

    def handle(self, *args, **opts):
        root = os.path.normpath(opts["root"])
        if not SourceFile.objects.filter(root=root).exists():
            raise CommandError(f"книг из этой папки в базе нет: {root}")
        now = timezone.now()
        n = 0
        for inv in Inventory.objects.filter(source_file__root=root, is_current=True).iterator():
            retire(inv, now)
            n += 1
        SourceFile.objects.filter(root=root).update(missing=True)
        # Состав актуальных сменился — дубли описей пересобираются (повторы снятых снова в своде).
        if n:
            res = dedup.run()
            self.stdout.write("дубли описей: " + ", ".join(f"{k} {v}" for k, v in res.items()))
        left = Inventory.objects.filter(is_current=True).count()
        self.stdout.write(f"снято с актуальных: {n}; актуальных описей осталось: {left}")
