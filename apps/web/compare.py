"""Сравнение описи с актуальной версией узла (ТЗ 1.5): что добавлено, убрано,
изменилось; свод по кодам «было / стало».

Позиция узнаётся по ключу (сжатый инвентарный номер); без номера — по
наименованию; повторы ключа различаются порядковым номером повтора.
"""

from collections import Counter, defaultdict
from decimal import Decimal

FIELDS = ("name", "recommended_code", "status__name", "qty", "cost_initial")


def _keyed(rows):
    seen = Counter()
    out = {}
    for r in rows:
        base = r["inv_key"] or "наим:" + (r["name"] or "").lower()
        seen[base] += 1
        out[(base, seen[base])] = r
    return out


def _rows(inv):
    return list(inv.items.values("sheet", "row_no", "inv_no", "inv_key", *FIELDS).order_by("sheet", "row_no"))


def code_summary(rows):
    """{(код, статус): [строк, количество]}"""
    s = defaultdict(lambda: [0, Decimal(0)])
    for r in rows:
        k = (r["recommended_code"] or "", r["status__name"] or "")
        s[k][0] += 1
        s[k][1] += r["qty"] or 0
    return s


def compare(new, old):
    new_rows = _rows(new)
    old_rows = _rows(old) if old else []
    a, b = _keyed(old_rows), _keyed(new_rows)
    added = [b[k] for k in b if k not in a]
    removed = [a[k] for k in a if k not in b]
    changed = []
    for k in b:
        if k in a:
            diffs = [(f, a[k][f], b[k][f]) for f in FIELDS if a[k][f] != b[k][f]]
            if diffs:
                changed.append({"new": b[k], "diffs": diffs})
    before, after = code_summary(old_rows), code_summary(new_rows)
    keys = sorted(set(before) | set(after))
    table = [{"code": c, "status": s,
              "before": before[(c, s)][1] if (c, s) in before else Decimal(0),
              "after": after[(c, s)][1] if (c, s) in after else Decimal(0)} for c, s in keys]
    for t in table:
        t["delta"] = t["after"] - t["before"]
    return {"added": added, "removed": removed, "changed": changed, "table": table,
            "rows_before": len(old_rows), "rows_after": len(new_rows)}
