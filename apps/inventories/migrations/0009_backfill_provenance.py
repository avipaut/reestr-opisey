"""Описям, загруженным до 27.10.2026: откуда загружены (путь файла), каким
обходом (по журналу обходов) и итоги по позициям. Размер и время изменения
файла — только где содержимое файла то же, что у описи (иначе файл с тех пор
переписан, и его размер к описи не относится)."""

import os

from django.conf import settings
from django.db import migrations

TOTALS = """
update inventories_inventory i set
    depreciation = coalesce(a.dep, 0), rows_excluded = a.ex, rows_no_code = a.nocode, codes = a.codes
from (select inventory_id,
             sum(depreciation) filter (where not excluded) dep,
             count(*) filter (where excluded) ex,
             count(*) filter (where recommended_code = '') nocode,
             count(distinct nullif(recommended_code, '')) codes
      from inventories_item group by inventory_id) a
where a.inventory_id = i.id
"""

SOURCE = """
update inventories_inventory i set
    loaded_via = case when sf.root = %(up)s then 'upload' else 'folder' end,
    loaded_from = case when sf.root = %(up)s then substr(sf.path, length(sf.root) + 2) else sf.path end,
    file_size = case when i.sha256 = sf.sha256 then sf.size end,
    file_mtime = case when i.sha256 = sf.sha256 then sf.mtime end
from inventories_sourcefile sf
where sf.id = i.source_file_id and i.loaded_via = ''
"""

RUNS = """
update inventories_inventory i set scan_run_id = f.run_id
from (select inventory_id, min(run_id) run_id from imports_scanfile
      where inventory_id is not null group by inventory_id) f
where f.inventory_id = i.id and i.scan_run_id is null
"""


def forward(apps, schema_editor):
    up = os.path.normpath(str(settings.UPLOAD_DIR))
    with schema_editor.connection.cursor() as c:
        c.execute("SET LOCAL statement_timeout = '1800s'")
        c.execute(TOTALS)
        c.execute(SOURCE, {"up": up})
        c.execute(RUNS)


class Migration(migrations.Migration):

    dependencies = [
        ('inventories', '0008_provenance'),
        ('imports', '0010_provenance'),
    ]

    operations = [
        migrations.RunPython(forward, migrations.RunPython.noop),
    ]
