"""Дерево учреждений: добавить узел, изменить код и имя (путь поддерева
пересчитывается), подсчёт описей и позиций по поддереву."""

from collections import defaultdict

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Concat, Substr

from apps.inventories.models import Inventory

from .models import Node


def add_node(parent, code, name, user=None):
    code, name = (code or "").strip(), (name or "").strip()
    if not code and not name:
        raise ValueError("нужен код или наименование")
    if code and Node.objects.filter(parent=parent, code=code).exists():
        raise ValueError(f"код «{code}» уже есть на этом уровне")
    node = Node(parent=parent, code=code, name=name, created_by=user)
    node.save()
    return node


@transaction.atomic
def edit_node(node, code, name):
    """Код меняет путь узла — пути всего поддерева меняются тем же началом."""
    code, name = (code or "").strip(), (name or "").strip()
    if not code and not name:
        raise ValueError("нужен код или наименование")
    if code and code != node.code and Node.objects.filter(parent=node.parent, code=code).exclude(pk=node.pk).exists():
        raise ValueError(f"код «{code}» уже есть на этом уровне")
    old_path = node.path
    node.code, node.name = code, name
    node.save()
    if node.path != old_path:
        # Меняется только начало пути: replace() задел бы и повтор кода ниже («/1/1/»).
        Node.objects.filter(path__startswith=old_path).exclude(pk=node.pk).update(
            path=Concat(Value(node.path), Substr(F("path"), len(old_path) + 1)))
    return node


def subtree_counts(parent):
    """{id ребёнка: (описей, позиций)} — по актуальным описям поддерева каждого ребёнка parent."""
    children = list(Node.objects.filter(parent=parent).values_list("id", "path"))
    if not children:
        return {}
    prefix = parent.path if parent else "/"
    rows = Inventory.objects.filter(is_current=True, node__path__startswith=prefix).values_list(
        "node__path", "rows")
    by_path = {p: i for i, p in children}
    out = defaultdict(lambda: [0, 0])
    depth = prefix.count("/")
    for path, n in rows:
        parts = path.split("/")
        child = "/".join(parts[:depth + 1]) + "/"  # путь ребёнка parent, в чьём поддереве опись
        cid = by_path.get(child)
        if cid:
            out[cid][0] += 1
            out[cid][1] += n
    return {i: tuple(out[i]) for i, _ in children}
