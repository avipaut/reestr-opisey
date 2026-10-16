"""Отчёт обхода в Excel: ошибки с путями, ошибки данных, пропущенные файлы,
исключённые позиции.

    manage.py report_scan [номер обхода] [--out файл.xlsx]    # без номера — последний законченный
"""

import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.imports.models import ScanRun
from apps.imports.report import build


class Command(BaseCommand):
    help = "Отчёт обхода в Excel"

    def add_arguments(self, parser):
        parser.add_argument("run", nargs="?", type=int)
        parser.add_argument("--out")

    def handle(self, run=None, out=None, **_):
        qs = ScanRun.objects.filter(finished_at__isnull=False)
        r = qs.filter(pk=run).first() if run else qs.order_by("-finished_at").first()
        if not r:
            raise CommandError("законченного обхода нет")
        data, n = build(r)
        out = out or os.path.join(settings.BASE_DIR, ".dev", f"Обход {r.pk} — ошибки и пропущенные.xlsx")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(data)
        self.stdout.write(f"обход {r.pk}: " + ", ".join(f"{k} {v}" for k, v in n.items()) + f"\n{out}")
