"""Свод версий: пересчёт при приёме и правке статуса, выборки «на дату»."""

from django.db.models import Count, Q, Sum

from apps.inventories.models import Item
from apps.orgs.models import Node

from .models import VersionStat


def open_stats(inventory, at):
    """Посчитать свод версии по её позициям и открыть строки с момента at."""
    rows = (Item.objects.filter(inventory=inventory, excluded=False)  # меры (материалы, запасы) — не штуки
            .values("recommended_code", "status_id", "priority", "object_type_id")
            .annotate(n=Count("id"), q=Sum("qty"), ci=Sum("cost_initial"), cb=Sum("cost_balance")))
    VersionStat.objects.bulk_create([
        VersionStat(inventory=inventory, node_id=inventory.node_id, code=r["recommended_code"] or "",
                    status_id=r["status_id"], priority=r["priority"], object_type_id=r["object_type_id"],
                    rows=r["n"], qty=r["q"] or 0, cost_initial=r["ci"] or 0, cost_balance=r["cb"] or 0,
                    valid_from=at)
        for r in rows
    ])


def close_stats(inventory, at):
    VersionStat.objects.filter(inventory=inventory, valid_to__isnull=True).update(valid_to=at)


def restat(inventory, at):
    """После правки статуса: закрыть прежний свод версии и открыть новый."""
    close_stats(inventory, at)
    open_stats(inventory, at)


def stats_at(at, node=None):
    """Строки свода, действовавшие в момент at (по поддереву узла, если задан)."""
    qs = VersionStat.objects.filter(Q(valid_to__isnull=True) | Q(valid_to__gt=at), valid_from__lte=at)
    if node is not None:
        qs = qs.filter(node__path__startswith=node.path)
    return qs


def by_code(at, node=None):
    """Свод «код × статус» на момент at: [{code, status, rows, qty}]."""
    return list(stats_at(at, node).values("code", "status__name")
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
    VersionStat.objects.filter(inventory=inventory).delete()
    for vf, vt in old:
        open_stats(inventory, vf)
        if vt:
            VersionStat.objects.filter(inventory=inventory, valid_from=vf, valid_to__isnull=True).update(valid_to=vt)

