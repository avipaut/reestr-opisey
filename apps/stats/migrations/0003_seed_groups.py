"""Группы кодов из сообщения пользователя 16.10.2026: «31122», «31123 12/13»."""

from django.db import migrations


def seed(apps, schema_editor):
    G = apps.get_model("stats", "CodeGroup")
    G.objects.get_or_create(name="31122", defaults={"prefixes": "31122", "order": 10})
    G.objects.get_or_create(name="31123 12/13", defaults={"prefixes": "3112312, 3112313", "order": 20})


class Migration(migrations.Migration):
    dependencies = [("stats", "0002_code_groups")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
