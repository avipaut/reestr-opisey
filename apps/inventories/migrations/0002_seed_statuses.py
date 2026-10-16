"""Статусы описи (формат — запись графа normalizovannaya-opis-format).
Дальше правит администратор; новый статус из описи добавляется при импорте."""

from django.db import migrations

STATUSES = ["Основной список", "Резерв", "Не требует оценки", "Вынесено", "Ручная валидация"]


def seed(apps, schema_editor):
    Status = apps.get_model("inventories", "Status")
    for i, name in enumerate(STATUSES, 1):
        Status.objects.get_or_create(name=name, defaults={"order": i * 10})


class Migration(migrations.Migration):
    dependencies = [("inventories", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
