"""Создать расписание обхода, если его нет. Существующее не трогается —
правка из админки не откатывается (граф APCS: debt-beat-admin-edits-reverted)."""

from django.core.management.base import BaseCommand
from django_q.models import Schedule

NAME = "Обход папок с описями"


class Command(BaseCommand):
    help = "Создать расписание обхода папок (если его ещё нет)"

    def add_arguments(self, parser):
        parser.add_argument("--minutes", type=int, default=5, help="интервал опроса, минут")

    def handle(self, *args, **opts):
        obj, created = Schedule.objects.get_or_create(
            name=NAME, defaults={"func": "apps.imports.tasks.scan_task",
                                 "schedule_type": Schedule.MINUTES, "minutes": opts["minutes"], "repeats": -1})
        self.stdout.write(("создано: " if created else "уже есть, не менялось: ")
                          + f"{obj.name}, каждые {obj.minutes} мин")
