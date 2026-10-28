"""Отметить подозрительные стоимости у загруженных описей и пересчитать их своды
(решение пользователя 28.10.2026). Пересчёт — функцией приложения, не историческими
моделями: свод считается по нынешним правилам; в пустой базе описей нет — ничего не делает."""

from django.db import migrations


def forward(apps, schema_editor):
    Item = apps.get_model("inventories", "Item")
    if not Item.objects.exists():
        return
    from apps.imports import suspects
    suspects.recheck()


class Migration(migrations.Migration):

    dependencies = [
        ('inventories', '0011_suspects'),
        ('stats', '0006_backfill_depreciation'),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
