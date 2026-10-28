"""Отметить подозрительные стоимости по нынешним порогам (SUSPECT_HIGH, SUSPECT_LOW) —
после их смены; подтверждённые («Верно») остаются подтверждёнными."""

from django.core.management.base import BaseCommand

from apps.imports import suspects


class Command(BaseCommand):
    help = "Отметить позиции с подозрительной первонач. стоимостью и пересчитать своды"

    def handle(self, *args, **opts):
        held, invs = suspects.recheck()
        self.stdout.write(f"отложено до проверки позиций: {held}; пересчитано описей: {invs}")
