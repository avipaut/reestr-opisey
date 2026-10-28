"""Износ в своде версий — по позициям версии с теми же кодом, статусом,
приоритетом и типом объекта (27.10.2026 правок статуса в базе не было, так
что разбивка по статусам у прежних сроков действия та же)."""

from django.db import migrations

SQL = """
update stats_versionstat v set depreciation = a.dep
from (select inventory_id, recommended_code code, coalesce(status_id, 0) st, coalesce(priority, -1) pr,
             coalesce(object_type_id, 0) ot, coalesce(sum(depreciation), 0) dep
      from inventories_item where not excluded group by 1, 2, 3, 4, 5) a
where a.inventory_id = v.inventory_id and a.code = v.code and a.st = coalesce(v.status_id, 0)
  and a.pr = coalesce(v.priority, -1) and a.ot = coalesce(v.object_type_id, 0)
"""


def forward(apps, schema_editor):
    with schema_editor.connection.cursor() as c:
        c.execute("SET LOCAL statement_timeout = '1800s'")
        c.execute(SQL)


class Migration(migrations.Migration):

    dependencies = [
        ('stats', '0005_depreciation'),
        ('inventories', '0009_backfill_provenance'),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
