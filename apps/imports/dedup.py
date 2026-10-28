"""Дубли описей — второй этап импорта (решение пользователя 28.10.2026: «Нам нужна
надежная обработка дубликатов. Например через обьединение множеств схожих описей»;
«вызывать его после импорта всего что попалось. Вторым этапом импорта»). ТЗ —
запись общего графа tz-reestr-opisey.

Повод: у одного учреждения в папке лежат листы одной книги и пересохранённые варианты
одной описи («ОСВ (2) — Sheet1» и «ОСВ (2) — ОС ДДХ»: 1 086 общих строк из 1 087), и
свод считал их дважды.

Как устроено. Проход идёт по всей базе разом — после обхода и отдельной командой
`manage.py dedup`, код один:

1. Кандидаты — актуальные описи одного узла.
2. Строка описи узнаётся по самому надёжному, что в ней есть: инв. номер (ключ позиции),
   стоимость, количество; без инв. номера — наименование (без регистра, «ё» = «е», без
   знаков), стоимость, количество. Описи связываются, если строк меньшей, найденных в
   большей (по номеру или по наименованию), не меньше DUP_LINK; группа — всё, что связано
   цепочкой.
3. Внутри группы строки объединяются как мультимножества: описи идут от большей к
   меньшей, строка, у которой в уже взятых описях есть пара, — повтор (dup_of), в свод
   не входит. Пара ищется ступенями: (1) инв. номер, стоимость, количество;
   (2) наименование, стоимость, количество — только если у одной из строк нет инв.
   номера (разные номера — разные вещи). Одинаковых строк внутри описи остаётся
   столько, сколько их больше всего в одной описи группы.
4. Разное количество или стоимость при том же инв. номере — разные позиции (ответа
   пользователя пока нет), строка помечается «сверить» (dup_check).

Пометки пересобираются целиком при каждом проходе: что было повтором, а теперь нет
(опись заменена новой версией, порог сменили), снова входит в свод.
"""

import logging
import re
from collections import defaultdict

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Count, Q, Sum

from apps.inventories.models import DupGroup, Inventory, Item
from apps.stats import services as stats

log = logging.getLogger(__name__)

_JUNK = re.compile(r"[\W_]+", re.U)
FIELDS = ("id", "inventory_id", "inv_key", "name", "cost_initial", "qty")


def norm_name(s):
    return _JUNK.sub(" ", (s or "").lower().replace("ё", "е")).strip()


def _num(v):
    """Число без хвостовых нулей: 500, 500.0 и 500.00 — одно и то же."""
    if v is None:
        return ""
    s = format(v, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def _keys(row):
    """(ключ ступени 1, ключ ступени 2) строки; None — ступень к строке неприменима."""
    _, _, inv_key, name, cost, qty = row
    c, q = _num(cost), _num(qty)
    k1 = ("i", inv_key, c, q) if inv_key else None
    n = norm_name(name)
    k2 = ("n", n, c, q) if n else None
    return k1, k2


def _groups(probe, allk, link):
    """Группы описей узла → [[описи]] (две и больше).

    probe — строки описи по самому надёжному ключу (инв. номер, без него — наименование);
    allk — все ключи её строк, обоих видов. Строка меньшей описи «есть» в большей, если её
    ключ есть среди ключей большей: так вариант описи без инв. номеров находит строки с
    номерами, а описи разных учреждений с одинаковыми стульями под разными номерами — нет."""
    ids = sorted(probe)
    owners = defaultdict(list)
    for i in ids:
        for k in allk[i]:
            owners[k].append(i)
    hits = defaultdict(int)  # (a, b) — строк a, найденных в b
    for a in ids:
        for k in probe[a]:
            for b in owners.get(k, ()):
                if b != a:
                    hits[(a, b)] += 1
    parent = {i: i for i in ids}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), n in hits.items():
        # Связь — по меньшей описи: её строки почти все есть в большей.
        if len(probe[a]) <= len(probe[b]) and n / len(probe[a]) >= link:
            parent[root(a)] = root(b)
    out = defaultdict(list)
    for i in ids:
        out[root(i)].append(i)
    return [g for g in out.values() if len(g) > 1]


def _merge(members, rows_by_inv):
    """Объединить строки группы. Возвращает (повторы {строка: (пара, ступень)},
    «сверить» {строка: cost|qty})."""
    kept1, kept2, kept_inv = defaultdict(list), defaultdict(list), {}  # ключ → взятые строки
    kept_by_inv_key = defaultdict(set)  # инв. номер → (стоимость, количество) взятых строк
    dups, check = {}, {}
    for inv in members:
        rows = rows_by_inv[inv]
        used = set()
        pending = []
        # Ступень 1: инв. номер, стоимость, количество.
        for row in rows:
            k1, k2 = _keys(row)
            hit = None
            if k1:
                hit = next((x for x in kept1[k1] if x not in used), None)
            if hit:
                used.add(hit)
                dups[row[0]] = (hit, 1)
            else:
                pending.append((row, k1, k2))
        # Ступень 2: наименование, стоимость, количество — если у одной из строк нет номера.
        fresh = []
        for row, k1, k2 in pending:
            hit = None
            if k2:
                hit = next((x for x in kept2[k2] if x not in used and not (row[2] and kept_inv[x])), None)
            if hit:
                used.add(hit)
                dups[row[0]] = (hit, 2)
            else:
                fresh.append((row, k1, k2))
        # Остальное — новые строки объединения; тот же номер с другой ценой или количеством — сверить.
        for row, k1, k2 in fresh:
            if row[2] and kept_by_inv_key.get(row[2]):
                cq = (_num(row[4]), _num(row[5]))
                if cq not in kept_by_inv_key[row[2]]:
                    check[row[0]] = "cost" if all(c != cq[0] for c, _ in kept_by_inv_key[row[2]]) else "qty"
        for row, k1, k2 in fresh:
            if k1:
                kept1[k1].append(row[0])
            if k2:
                kept2[k2].append(row[0])
            kept_inv[row[0]] = row[2]
            if row[2]:
                kept_by_inv_key[row[2]].add((_num(row[4]), _num(row[5])))
    return dups, check


def run(link=None, progress=None, nodes=None):
    """Пересобрать группы и пометки повторов — по всей базе или по узлам nodes (группа
    всегда внутри узла, поэтому узел пересобирается так же точно, как вся база: так
    ручной приём одной описи не ждёт прохода по всем). Возвращает итог словарём."""
    link = settings.DUP_LINK if link is None else link
    scope = Q() if nodes is None else Q(node_id__in=list(nodes))
    item_scope = Q() if nodes is None else Q(inventory__node_id__in=list(nodes))
    nodes = list(Inventory.objects.filter(scope, is_current=True, node__isnull=False).values("node_id")
                 .annotate(n=Count("id")).filter(n__gt=1).values_list("node_id", flat=True))
    before = dict(Inventory.objects.filter(scope, rows_dup__gt=0).values_list("id", "rows_dup"))
    marks, checks, groups = {}, {}, []
    for done, node_id in enumerate(nodes, 1):
        invs = list(Inventory.objects.filter(is_current=True, node_id=node_id).values_list("id", "rows"))
        size = dict(invs)
        rows_by_inv = defaultdict(list)
        for row in Item.objects.filter(inventory_id__in=size).values_list(*FIELDS).iterator(chunk_size=20000):
            rows_by_inv[row[1]].append(row)
        probe, allk = {}, {}
        for i in size:
            p, a = set(), set()
            for row in rows_by_inv[i]:
                k1, k2 = _keys(row)
                if k1 or k2:
                    p.add(k1 or k2)
                a.update(k for k in (k1, k2) if k)
            probe[i], allk[i] = p, a
        for members in _groups(probe, allk, link):
            members.sort(key=lambda i: (-len(rows_by_inv[i]), i))
            dups, check = _merge(members, rows_by_inv)
            marks.update(dups)
            checks.update(check)
            groups.append((node_id, members, dups))
        del rows_by_inv
        if progress:
            progress(done, len(nodes))

    with transaction.atomic():
        Item.objects.filter(item_scope, dup_of__isnull=False).update(dup_of=None, dup_step=0)
        Item.objects.filter(item_scope).exclude(dup_check="").update(dup_check="")
        Inventory.objects.filter(scope, dup_group__isnull=False).update(dup_group=None)
        DupGroup.objects.filter(scope).delete()
        _write_marks(marks)
        by_check = defaultdict(list)
        for item, why in checks.items():
            by_check[why].append(item)
        for why, ids in by_check.items():
            for a in range(0, len(ids), 5000):
                Item.objects.filter(pk__in=ids[a:a + 5000]).update(dup_check=why)
        made = []
        for node_id, members, dups in groups:
            g = DupGroup.objects.create(node_id=node_id, base_id=members[0])
            Inventory.objects.filter(pk__in=members).update(dup_group=g)
            made.append(g.pk)
        touched = set(before) | {i for _, members, dups in groups for i in members}
        for inv in Inventory.objects.filter(pk__in=touched):
            stats.fill_totals(inv)
            if inv.rows_dup != before.get(inv.pk, 0):
                stats.rebuild_stats(inv)
        _group_totals(made)
    return {"групп": len(groups), "описей в группах": sum(len(m) for _, m, _ in groups),
            "повторов строк": len(marks), "сверить": len(checks), "узлов": len(nodes)}


def _write_marks(marks):
    """Повторов — сотни тысяч: пишутся через временную таблицу одним обновлением."""
    if not marks:
        return
    with connection.cursor() as c:
        c.execute("create temp table _dup (item bigint primary key, of bigint, step smallint) on commit drop")
        rows = [(i, of, step) for i, (of, step) in marks.items()]
        for a in range(0, len(rows), 10000):
            part = rows[a:a + 10000]
            c.execute("insert into _dup values " + ",".join(["(%s,%s,%s)"] * len(part)),
                      [x for r in part for x in r])
        c.execute(f"update {Item._meta.db_table} i set dup_of_id=d.of, dup_step=d.step from _dup d where i.id=d.item")


def _group_totals(ids):
    """Итоги группы: строк и первонач. стоимости во всех описях и после объединения
    (меры и подозрительные — как в своде, не считаются)."""
    base = Q(excluded=False, held=False)
    for g in DupGroup.objects.filter(pk__in=ids):
        agg = Item.objects.filter(base, inventory__dup_group=g).aggregate(
            rb=Count("id"), ra=Count("id", filter=Q(dup_of__isnull=True)),
            cb=Sum("cost_initial"), ca=Sum("cost_initial", filter=Q(dup_of__isnull=True)))
        DupGroup.objects.filter(pk=g.pk).update(rows_before=agg["rb"] or 0, rows_after=agg["ra"] or 0,
                                                cost_before=agg["cb"] or 0, cost_after=agg["ca"] or 0)
