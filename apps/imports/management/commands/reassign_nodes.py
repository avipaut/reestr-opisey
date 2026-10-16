"""Разложить по узлам описи без узла — по пути папки книги, без чтения файлов."""

from django.core.management.base import BaseCommand

from apps.imports.services import reassign_nodes


class Command(BaseCommand):
    help = "Определить узел описей без узла по пути папки (узлы создаются)"

    def handle(self, *args, **opts):
        done, left = reassign_nodes()
        self.stdout.write(f"разложено по узлам: {done}; без узла осталось: {left}")
