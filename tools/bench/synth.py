"""Синтетический объём для замера (ТЗ 9.3): учреждения × подведы, по описи
на подвед, у части — прежние версии; позиции и своды версий. Всё — SQL в
самой базе (generate_series), без книг.

    set DB_NAME=reestr_bench
    .venv\\Scripts\\python tools\\bench\\synth.py --orgs 100 --subs 20 --items 1500 --history 0.3
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()
from django.db import connection  # noqa: E402

CODES = ["31122110", "31122190", "31123110", "31123120", "31123130", "31123190", "31132190", "31121110",
         "31112120", "31113110", "0"]


def sql(c, q, *a):
    t = time.perf_counter()
    c.execute(q, a or None)
    print(f"  {time.perf_counter() - t:7.1f} с  {q.strip().splitlines()[0][:90]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orgs", type=int, default=100)
    ap.add_argument("--subs", type=int, default=20)
    ap.add_argument("--items", type=int, default=1500)
    ap.add_argument("--history", type=float, default=0.3, help="доля подведов с двумя прежними версиями")
    a = ap.parse_args()
    if connection.settings_dict["NAME"] == "reestr":
        sys.exit("Синтетика — только в отдельной базе (DB_NAME=reestr_bench)")
    with connection.cursor() as c:
        c.execute("SET statement_timeout = 0")
        sql(c, """TRUNCATE stats_versionstat, inventories_itemstatuschange, inventories_item, inventories_rule,
                  inventories_reconciliation, inventories_inventory, inventories_sourcefile, orgs_foldermapping,
                  orgs_node RESTART IDENTITY CASCADE""")
        sql(c, """INSERT INTO orgs_node (parent_id, code, name, level, path, created_at)
                  SELECT NULL, g::text, 'Учреждение ' || g, 0, '/' || g || '/', now()
                  FROM generate_series(1, %s) g""", a.orgs)
        sql(c, """INSERT INTO orgs_node (parent_id, code, name, level, path, created_at)
                  SELECT p.id, s::text, 'Подвед ' || p.code || '.' || s, 1, p.path || s || '/', now()
                  FROM orgs_node p CROSS JOIN generate_series(1, %s) s WHERE p.level = 0""", a.subs)
        # Версии: у каждого подведа — актуальная; у доли — ещё две прежние.
        sql(c, """CREATE TEMP TABLE nv AS
                  SELECT id AS node_id, path, CASE WHEN random() < %s THEN 3 ELSE 1 END AS nver
                  FROM orgs_node WHERE level = 1""", a.history)
        sql(c, """CREATE TEMP TABLE v AS
                  SELECT nv.node_id, nv.path, k AS ver, nv.nver FROM nv CROSS JOIN LATERAL generate_series(1, nv.nver) k""")
        sql(c, """INSERT INTO inventories_sourcefile (path, root, rel_folder, name, size, mtime, sha256, first_seen,
                                                    last_seen, missing)
                  SELECT 'X:\\' || path || 'v' || ver || '.xlsx', 'X:\\', path, 'v' || ver || '.xlsx', 100000,
                         now(), md5(path || ver) || md5(ver || path), now(), now(), false FROM v""")
        sql(c, """INSERT INTO inventories_inventory (state, node_id, proposed_path, node_resolved, source_file_id,
                     sha256, content_hash, error, parsed_at, valid_from, valid_to, is_current, org_text, source_name,
                     source_sheet, source_kind, source_hierarchy, source_folder, cost_basis, check_flag, summary_lines,
                     unknown_columns, rows, qty, cost_initial, cost_balance)
                  SELECT 'accepted', v.node_id, '[]', true, sf.id, sf.sha256, md5(sf.path), '', now(),
                         now() - make_interval(days => (v.nver - v.ver) * 120 + 10),
                         CASE WHEN v.ver < v.nver THEN now() - make_interval(days => (v.nver - v.ver - 1) * 120 + 10) END,
                         v.ver = v.nver, '', '', '', '', '', '', '', false, '[]', '[]', %s, %s, 0, 0
                  FROM v JOIN inventories_sourcefile sf ON sf.rel_folder = v.path AND sf.name = 'v' || v.ver || '.xlsx'""",
            a.items, a.items)
        sql(c, f"""INSERT INTO inventories_item (inventory_id, sheet, row_no, name, account_code, qty, qty_note, inv_no,
                     inv_key, cost_initial, depreciation, cost_balance, priority, mol, recommended_code, department_text,
                     suborg_text, model, working, status_id, status_doc, reason, comment, object_type_id,
                     fully_depreciated, subdivision, org_code, source_row, source_extra, user_mark, name_fill, row_hash,
                     extra)
                  SELECT i.id, 'Опись', r + 1,
                         (ARRAY['Компьютер','Принтер','Стол','Стул','Насос','Кондиционер','Сервер','Монитор',
                                'Сейф','Генератор'])[1 + mod(r, 10)] || ' модель ' || mod(r * 7919, 1000),
                         '31122110', 1, '', (r + 1)::text, (r + 1)::text,
                         round((random() * 500000)::numeric, 2), 0, round((random() * 300000)::numeric, 2),
                         1 + mod(r, 5), '', (ARRAY{CODES!r})[1 + mod(r * 31 + i.id, {len(CODES)})], '', 'Подвед', '', '',
                         (SELECT id FROM inventories_status ORDER BY id LIMIT 1 OFFSET mod(r + i.id, 4)),
                         '', 'причина', '', NULL, NULL, '', '31122110', r + 10, '', '', '', md5(i.id || '-' || r), '{{}}'
                  FROM inventories_inventory i CROSS JOIN generate_series(0, %s - 1) r""", a.items)
        sql(c, """INSERT INTO stats_versionstat (inventory_id, node_id, code, status_id, priority, object_type_id, rows,
                     qty, cost_initial, cost_balance, valid_from, valid_to)
                  SELECT it.inventory_id, i.node_id, it.recommended_code, it.status_id, it.priority, it.object_type_id,
                         count(*), sum(it.qty), coalesce(sum(it.cost_initial), 0), coalesce(sum(it.cost_balance), 0),
                         i.valid_from, i.valid_to
                  FROM inventories_item it JOIN inventories_inventory i ON i.id = it.inventory_id
                  GROUP BY it.inventory_id, i.node_id, it.recommended_code, it.status_id, it.priority,
                           it.object_type_id, i.valid_from, i.valid_to""")
        sql(c, "ANALYZE")
        c.execute("""SELECT (SELECT count(*) FROM orgs_node), (SELECT count(*) FROM inventories_inventory),
                            (SELECT count(*) FROM inventories_item), (SELECT count(*) FROM stats_versionstat),
                            pg_size_pretty(pg_database_size(current_database()))""")
        print("узлов %s, описей %s, позиций %s, строк свода %s, база %s" % c.fetchone())


if __name__ == "__main__":
    main()
