"""Дубли описей по уже готовой базе — тот же второй этап, что идёт после обхода
(apps.imports.dedup). Итог смотрят и выгружают на странице «Дубли описей»."""

import time

from django.core.management.base import BaseCommand

from apps.imports import dedup


class Command(BaseCommand):
    help = "Пересобрать группы похожих описей и пометки повторов строк; пересчитать своды"

    def add_arguments(self, parser):
        parser.add_argument("--link", type=float, help="доля общих строк меньшей описи (по умолчанию DUP_LINK)")

    def handle(self, *args, **opts):
        t = time.time()
        res = dedup.run(link=opts.get("link"),
                        progress=lambda d, n: d % 50 == 0 and self.stdout.write(f"  узлов {d} из {n}"))
        self.stdout.write(", ".join(f"{k} {v}" for k, v in res.items()) + f"; {time.time() - t:.0f} с")
