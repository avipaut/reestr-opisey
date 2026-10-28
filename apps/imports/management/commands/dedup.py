"""Дубли описей по уже готовой базе — тот же второй этап, что идёт после обхода
(apps.imports.dedup). С --report — выгрузка групп в Excel для проверки на глаз."""

import time

from django.core.management.base import BaseCommand

from apps.imports import dedup


class Command(BaseCommand):
    help = "Пересобрать группы похожих описей и пометки повторов строк; пересчитать своды"

    def add_arguments(self, parser):
        parser.add_argument("--link", type=float, help="доля общих строк меньшей описи (по умолчанию DUP_LINK)")
        parser.add_argument("--report", help="путь .xlsx: группы, описи, строки «сверить»")

    def handle(self, *args, **opts):
        t = time.time()
        res = dedup.run(link=opts.get("link"),
                        progress=lambda d, n: d % 50 == 0 and self.stdout.write(f"  узлов {d} из {n}"))
        self.stdout.write(", ".join(f"{k} {v}" for k, v in res.items()) + f"; {time.time() - t:.0f} с")
        if opts.get("report"):
            from apps.imports.dedup_report import write
            write(opts["report"])
            self.stdout.write(f"выгрузка: {opts['report']}")
