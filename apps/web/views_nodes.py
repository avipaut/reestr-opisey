"""Учреждения: дерево с подгрузкой по уровням, поиск с полным путём,
добавление и правка узлов; данные для общего выбора узла (nodepicker.js)."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.inventories.models import Inventory
from apps.orgs import services
from apps.orgs.models import Node


def _node_json(n, counts=None):
    c = (counts or {}).get(n.pk, (0, 0))
    return {"id": n.pk, "code": n.code, "name": n.name, "label": n.label, "trail": n.trail,
            "has_children": getattr(n, "kids", None) if getattr(n, "kids", None) is not None else n.children.exists(),
            "inventories": c[0], "rows": c[1]}


@login_required
def nodes_children(request):
    pid = request.GET.get("parent") or ""
    parent = get_object_or_404(Node, pk=pid) if pid.isdigit() else None
    kids = list(Node.objects.filter(parent=parent).order_by("path"))
    has = set(Node.objects.filter(parent__in=kids).values_list("parent_id", flat=True))
    for k in kids:
        k.kids = k.pk in has
    Node.fill_trails([*kids, parent])
    counts = services.subtree_counts(parent)
    return JsonResponse({"parent": _node_json(parent) if parent else None,
                         "crumbs": [{"id": c.pk, "label": c.label} for c in _crumbs(parent)],
                         "items": [_node_json(k, counts) for k in kids]})


@login_required
def nodes_search(request):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"items": []})
    qs = Node.objects.filter(Q(code=q) | Q(code__startswith=q) | Q(search__contains=q.lower().replace("ё", "е")))
    found = Node.fill_trails(list(qs.order_by("level", "path")[:30]))
    return JsonResponse({"items": [{"id": n.pk, "label": n.label, "trail": n.trail} for n in found]})


def _crumbs(node):
    out = []
    while node is not None:
        out.append(node)
        node = node.parent
    return list(reversed(out))


@login_required
def nodes_page(request):
    sel = Node.objects.filter(pk=request.GET.get("node")).first() if (request.GET.get("node") or "").isdigit() else None
    ctx = {"sel": sel, "nav": "nodes"}
    if sel:
        ctx.update(
            crumbs=_crumbs(sel),
            current=Inventory.objects.filter(node=sel, is_current=True).first(),
            versions=Inventory.objects.filter(node=sel, state=Inventory.State.ACCEPTED).count(),
            children=sel.children.order_by("path"),
            sub_inv=Inventory.objects.filter(is_current=True, node__path__startswith=sel.path).count(),
            open_ids=[c.pk for c in _crumbs(sel)])
    return render(request, "web/nodes.html", ctx)


@login_required
@permission_required("orgs.add_node", raise_exception=True)
@require_POST
def node_add(request):
    pid = request.POST.get("parent") or ""
    parent = get_object_or_404(Node, pk=pid) if pid.isdigit() else None
    try:
        n = services.add_node(parent, request.POST.get("code"), request.POST.get("name"), request.user)
    except ValueError as e:
        messages.error(request, f"Не добавлено: {e}")
        return redirect(f"/nodes/?node={parent.pk}" if parent else "/nodes/")
    messages.success(request, f"Добавлено: {n.trail}")
    return redirect(f"/nodes/?node={n.pk}")


@login_required
@permission_required("orgs.change_node", raise_exception=True)
@require_POST
def node_edit(request, pk):
    n = get_object_or_404(Node, pk=pk)
    try:
        services.edit_node(n, request.POST.get("code"), request.POST.get("name"))
    except ValueError as e:
        messages.error(request, f"Не изменено: {e}")
    else:
        messages.success(request, f"Сохранено: {n.trail}")
    return redirect(f"/nodes/?node={n.pk}")
