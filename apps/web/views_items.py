"""Импорт, реестр описей, опись с позициями, поиск позиций, карточка позиции."""

import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.imports import services as imports
from apps.imports.models import ScanRun
from apps.inventories.models import Inventory, Item, ItemStatusChange, Status
from apps.orgs.models import Node
from apps.stats import services as stats

from .filters import ItemFilter, choices, inventories_at, when
from .xlsx import items_xlsx_response

PAGE = 100


def _page(request, qs, size=PAGE):
    return Paginator(qs, size).get_page(request.GET.get("page"))


def _totals(qs):
    return qs.aggregate(rows=Count("id"), qty=Sum("qty"), cost=Sum("cost_initial"), bal=Sum("cost_balance"))


# ------------------------------------------------------------------ импорт

@login_required
def import_page(request):
    roots = [{"path": r, "ok": os.path.isdir(r)} for r in settings.INVENTORY_ROOTS]
    uploads = (Inventory.objects.filter(source_file__root="загрузка").select_related("source_file", "node")
               .order_by("-parsed_at")[:20])
    return render(request, "web/import.html", {
        "roots": roots, "runs": ScanRun.objects.all()[:5], "uploads": uploads, "nav": "import"})


@login_required
@permission_required("inventories.add_inventory", raise_exception=True)
@require_POST
def import_upload(request):
    files = request.FILES.getlist("files")
    node = Node.objects.filter(pk=request.POST.get("node")).first() if request.POST.get("node") else None
    if not files:
        messages.error(request, "Файлы не выбраны")
        return redirect("import_page")
    ok, last = 0, None
    for f in files:
        if os.path.splitext(f.name)[1].lower() not in settings.INVENTORY_EXTENSIONS:
            messages.error(request, f"{f.name}: не книга Excel (.xlsx, .xlsm)")
            continue
        inv, note = imports.stage_upload(f, node, request.user)
        if inv is None:
            messages.warning(request, f"{f.name}: {note}")
            continue
        if inv.state == Inventory.State.ERROR:
            messages.error(request, f"{f.name}: {inv.error}")
            continue
        ok += 1
        last = inv
        messages.success(request, f"{f.name}: {inv.get_state_display()}" + (f" — {note}" if note else ""))
    if ok == 1 and last.state == Inventory.State.PENDING:
        return redirect("review_detail", pk=last.pk)
    return redirect("review_list" if ok else "import_page")


# ------------------------------------------------------------------ описи

@login_required
def inventories(request):
    at, date = when(request)
    qs = inventories_at(at).select_related("node", "source_file").annotate(
        versions=Count("node__inventories", filter=Q(node__inventories__state=Inventory.State.ACCEPTED)))
    node = Node.objects.filter(pk=request.GET.get("node")).first() if (request.GET.get("node") or "").isdigit() else None
    if node:
        qs = qs.filter(node__path__startswith=node.path)
    q = (request.GET.get("q") or "").strip()
    if q:
        low = q.lower().replace("ё", "е")
        qs = qs.filter(Q(node__search__contains=low) | Q(node__code=q) | Q(source_file__name__contains=q)
                       | Q(org_text__contains=q))
    qs = qs.order_by("node__path")
    tot = qs.aggregate(n=Count("id"), rows=Sum("rows"), qty=Sum("qty"), cost=Sum("cost_initial"))
    return render(request, "web/inventories.html", {
        "page": _page(request, qs, 50), "tot": tot, "node": node, "q": q, "date": date,
        "qs_base": request.GET.copy(), "nav": "inventories"})


@login_required
def inventory_detail(request, pk):
    inv = get_object_or_404(Inventory.objects.select_related("node", "source_file", "decided_by"), pk=pk)
    f = ItemFilter(request, base=inv.items.all())
    qs = f.qs.select_related("status", "object_type").order_by("sheet", "row_no")
    if request.GET.get("export") == "xlsx":
        return items_xlsx_response(f"Опись {inv.node.label if inv.node else inv.pk}", qs,
                                   [("Опись", inv.source_file.name), ("Узел", inv.node.trail if inv.node else "—"),
                                    *f.params()[1:]], request)
    versions = (Inventory.objects.filter(node=inv.node, state=Inventory.State.ACCEPTED).order_by("-valid_from")
                if inv.node_id else [])
    return render(request, "web/inventory_detail.html", {
        "inv": inv, "f": f, "page": _page(request, qs), "tot": _totals(f.qs), "versions": versions,
        "rules": inv.rules.all(), **choices(), "nav": "inventories"})


# ------------------------------------------------------------------ поиск

@login_required
def items_search(request):
    f = ItemFilter(request)
    qs = f.qs.select_related("status", "object_type", "inventory__node__parent__parent").order_by("inventory__node__path",
                                                                                  "sheet", "row_no")
    if request.GET.get("export") == "xlsx":
        return items_xlsx_response("Выборка позиций", qs, f.params(), request)
    ctx = {"f": f, **choices(), "nav": "items"}
    if f.active:
        ctx.update(page=_page(request, qs), tot=_totals(f.qs))
    return render(request, "web/items.html", ctx)


@login_required
def item_detail(request, pk):
    it = get_object_or_404(Item.objects.select_related("inventory__node", "inventory__source_file", "status",
                                                       "object_type"), pk=pk)
    fields = [(f.verbose_name, getattr(it, f.name)) for f in Item._meta.fields
              if f.name not in ("id", "inventory", "row_hash", "extra", "inv_key")]
    return render(request, "web/item_detail.html", {
        "it": it, "fields": fields, "extra": it.extra, "statuses": Status.objects.filter(is_active=True),
        "changes": it.status_changes.select_related("old", "new", "by").order_by("-at"), "nav": "items",
        "back": request.GET.get("back", "")})


@login_required
@permission_required("inventories.change_item", raise_exception=True)
@require_POST
def item_status(request, pk):
    """Правка статуса администратором (ТЗ 4.2): журнал и пересчёт свода версии."""
    it = get_object_or_404(Item.objects.select_related("inventory"), pk=pk)
    new = get_object_or_404(Status, pk=request.POST.get("status"))
    if new.pk == it.status_id:
        messages.info(request, "Статус не изменился")
        return redirect("item_detail", pk=pk)
    with transaction.atomic():
        ItemStatusChange.objects.create(item=it, old_id=it.status_id, new=new, by=request.user,
                                        comment=request.POST.get("comment", "")[:2000])
        it.status = new
        it.save(update_fields=["status"])
        if it.inventory.state == Inventory.State.ACCEPTED and it.inventory.is_current:
            stats.restat(it.inventory, timezone.now())
    messages.success(request, f"Статус изменён: {new}")
    return redirect("item_detail", pk=pk)
