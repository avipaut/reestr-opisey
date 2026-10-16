"""Обход папок с описями — та же функция, что у расписания и кнопки.
Код выхода — не 0, если обход прерван или были ошибки (граф APCS:
debt-import-swallows-failures)."""

import sys

from django.core.management.base import BaseCommand

from apps.imports.models import ScanRun
from apps.imports.services import scan


class Command(BaseCommand):
    help = "Обойти папки INVENTORY_ROOTS (или указанные) и поставить новые описи в просмотр"

    def add_arguments(self, parser):
        parser.add_argument("roots", nargs="*", help="папки (по умолчанию — INVENTORY_ROOTS)")
        parser.add_argument("--workers", type=int, default=None, help="процессов разбора")

    def handle(self, *args, **opts):
        run = scan(opts["roots"] or None, opts["workers"])
        self.stdout.write(
            f"{run.get_state_display()}: найдено {run.found}, проигнорировано {run.ignored}, "
            f"без изменений {run.unchanged}, копий {run.moved}, разобрано {run.parsed}, принято {run.accepted}, "
            f"ждут просмотра {run.pending}, "
            f"строки как в актуальной {run.same_content}, ошибок {run.errors}, пропало {run.missing}, "
            f"{(run.finished_at - run.started_at).total_seconds():.1f} с")
        if run.log:
            self.stdout.write(run.log)
        if run.state in (ScanRun.State.FAILED, ScanRun.State.DONE_ERRORS, ScanRun.State.BUSY):
            sys.exit(1)
