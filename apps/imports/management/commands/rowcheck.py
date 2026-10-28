"""Проверка каждой строки актуальных описей (apps.imports.rowcheck): только подсчёт —
база не меняется. Отметки, строк, первонач. стоимость, примеры."""

import time

from django.core.management.base import BaseCommand

from apps.imports import rowcheck


class Command(BaseCommand):
    help = "Проверить каждую строку актуальных описей и вывести, что найдено (без записи)"

    def add_arguments(self, parser):
        parser.add_argument("--samples", type=int, default=4)

    def handle(self, *args, **opts):
        t = time.time()
        total, stat = rowcheck.analyze(samples=opts["samples"],
                                       progress=lambda d, n: d % 200 == 0 and self.stdout.write(f"  узлов {d} из {n}"))
        self.stdout.write(f"строк проверено: {total}; {time.time() - t:.0f} с")
        for flag, (n, money, ex) in sorted(stat.items(), key=lambda x: -x[1][0]):
            self.stdout.write(f"\n== {flag}: строк {n}, первонач. стоимость {money:,.0f}".replace(",", " "))
            for rid, inv, name, qty, ci, extra in ex:
                self.stdout.write(f"   [{inv}/{rid}] {name} | кол-во {qty} | {ci} | {extra}")
