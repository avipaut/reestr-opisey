"""Свод версий: пересчёт при приёме и правке статуса, выборки «на дату»."""

from django.db.models import BooleanField, Count, ExpressionWrapper, Q, Sum

from apps.inventories.models import Inventory, Item
from apps.orgs.models import Node

from .flags import DEFAULT, Flags  # noqa: F401  (Flags — для вызывающих)
from .models import VersionStat

# В свод входят штуки (не меры) и не отложенные: подозрительная стоимость, не подтверждённая
# на вкладке «Подозрительные» (решение пользователя 28.10.2026), и не повторы строк другой
# описи той же группы похожих описей и не строки устаревшей описи — в группе есть опись
# более нового года (apps.imports.dedup, решения 28.10.2026).
# Правило — apps.stats.flags (флаги «учитывать»), здесь — свод по умолчанию.
COUNTED = DEFAULT.item_q()


def _kind(q):
    return ExpressionWrapper(q, output_field=BooleanField())


def open_stats(inventory, at):
    """Посчитать свод версии по её позициям и открыть строки с момента at. В своде — все
    позиции, вид строки (мера, подозрительная, повтор) — ключ строки свода: флаги
    «учитывать» отбирают строки свода, не пересчитывая позиции."""
    rows = (Item.objects.filter(inventory=inventory)
            .annotate(k_measure=_kind(Q(excluded=True)), k_suspect=_kind(Q(excluded=False, held=True)),
                      k_dup=_kind(Q(dup_of__isnull=False)))
            .values("recommended_code", "status_id", "priority", "object_type_id", "k_measure", "k_suspect", "k_dup")
            .annotate(n=Count("id"), q=Sum("qty"), ci=Sum("cost_initial"), cb=Sum("cost_balance"),
                      dep=Sum("depreciation")))
    VersionStat.objects.bulk_create([
        VersionStat(inventory=inventory, node_id=inventory.node_id, code=r["recommended_code"] or "",
                    status_id=r["status_id"], priority=r["priority"], object_type_id=r["object_type_id"],
                    rows=r["n"], qty=r["q"] or 0, cost_initial=r["ci"] or 0, cost_balance=r["cb"] or 0,
                    depreciation=r["dep"] or 0, measure=r["k_measure"], suspect=r["k_suspect"], dup=r["k_dup"],
                    valid_from=at)
        for r in rows
    ])


TOTALS = ["rows", "qty", "cost_initial", "cost_balance", "depreciation", "rows_excluded", "rows_held",
          "rows_no_code", "codes", "rows_dup"]


def fill_totals(inventory):
    """Итоги описи по её позициям. Количество и деньги — без мер (как в своде);
    строк — все, меры и отложенные (подозрительные) отдельно."""
    inc = COUNTED
    agg = Item.objects.filter(inventory=inventory).aggregate(
        rows=Count("id"), qty=Sum("qty", filter=inc), cost_initial=Sum("cost_initial", filter=inc),
        cost_balance=Sum("cost_balance", filter=inc), depreciation=Sum("depreciation", filter=inc),
        rows_excluded=Count("id", filter=Q(excluded=True)), rows_held=Count("id", filter=Q(excluded=False, held=True)),
        rows_no_code=Count("id", filter=Q(recommended_code="")),
        rows_dup=Count("id", filter=Q(dup_of__isnull=False)),
        codes=Count("recommended_code", distinct=True, filter=~Q(recommended_code="")))
    for f in TOTALS:
        setattr(inventory, f, agg[f] or 0)
    Inventory.objects.filter(pk=inventory.pk).update(**{f: getattr(inventory, f) for f in TOTALS})
    return inventory


def close_stats(inventory, at):
    VersionStat.objects.filter(inventory=inventory, valid_to__isnull=True).update(valid_to=at)


def restat(inventory, at):
    """После правки статуса: закрыть прежний свод версии и открыть новый."""
    close_stats(inventory, at)
    open_stats(inventory, at)


def stats_at(at, node=None, flags=DEFAULT):
    """Строки свода, действовавшие в момент at (по поддереву узла, если задан), — тех видов
    позиций, что учитывают флаги (по умолчанию — свод без повторов, устаревших описей, мер
    и подозрительных); flags=None — все строки."""
    qs = VersionStat.objects.filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at), valid_from__lte=at)
    if flags is not None:
        qs = qs.filter(flags.stat_q())
    if node is not None:
        qs = qs.filter(node__path__startswith=node.path)
    return qs


def by_code(at, node=None, flags=DEFAULT):
    """Свод «код × статус» на момент at: [{code, status, rows, qty}]."""
    return list(stats_at(at, node, flags).values("code", "status__name")
                .annotate(rows=Sum("rows"), qty=Sum("qty")).order_by("code", "status__name"))


def by_code_and_node(at, level_parent=None):
    """Свод «код × узел» на момент at: узлы — дети level_parent (или учреждения),
    у каждого — сумма по его поддереву."""
    nodes = Node.objects.filter(parent=level_parent).order_by("path")
    out = []
    for n in nodes:
        for r in stats_at(at, n).values("code").annotate(qty=Sum("qty"), rows=Sum("rows")):
            out.append({"node": n, **r})
    return out


def code_groups():
    from .models import CodeGroup
    return list(CodeGroup.objects.filter(is_active=True))


def group_sums(groups, values_by_code, width):
    """[(группа, [сумма по каждому из width значений])] — по кодам с нужным началом.
    values_by_code: {код: [width значений]}."""
    out = []
    for g in groups:
        acc = [0] * width
        for code, vals in values_by_code.items():
            if g.matches(code):
                acc = [a + (v or 0) for a, v in zip(acc, vals)]
        out.append((g, acc))
    return out


def rebuild_stats(inventory):
    """Пересчитать свод версии после исправления данных — с прежним сроком
    действия (это исправление, а не новое событие истории)."""
    old = list(VersionStat.objects.filter(inventory=inventory).values_list("valid_from", "valid_to").distinct())
    # Строк свода нет, когда все позиции были вне свода (все повторы других описей или все
    # подозрительные) — тогда срок действия берётся у самой версии описи, иначе вернувшиеся
    # в свод позиции некуда открыть.
    if not old and inventory.valid_from:
        old = [(inventory.valid_from, inventory.valid_to)]
    VersionStat.objects.filter(inventory=inventory).delete()
    for vf, vt in old:
        open_stats(inventory, vf)
        if vt:
            VersionStat.objects.filter(inventory=inventory, valid_from=vf, valid_to__isnull=True).update(valid_to=vt)



def inventory_sums(at, inventory_ids, flags=DEFAULT, statuses=None):
    """Суммы описей по своду на момент at и флагам: {опись: {rows, qty, ci, cb, dep, out}};
    out — позиций, оставленных флагами за счётом. Описи без строк свода (и без отобранных
    статусов) в ответ не попадают."""
    inc = flags.stat_q()
    qs = stats_at(at, flags=None).filter(inventory_id__in=list(inventory_ids))
    if statuses:
        qs = qs.filter(status__in=statuses)
    out = {}
    for r in qs.values("inventory_id").annotate(
            x_rows=Sum("rows", filter=inc), x_qty=Sum("qty", filter=inc), x_ci=Sum("cost_initial", filter=inc),
            x_cb=Sum("cost_balance", filter=inc), x_dep=Sum("depreciation", filter=inc), x_all=Sum("rows")).order_by():
        r = {k[2:] if k.startswith("x_") else k: v or 0 for k, v in r.items()}
        r["out"] = r["all"] - r["rows"]
        out[r.pop("inventory_id")] = r
    return out
