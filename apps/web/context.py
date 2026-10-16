"""Число описей на просмотр — в меню каждого экрана."""

from apps.inventories.models import Inventory


def nav(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"pending_count": Inventory.objects.filter(state=Inventory.State.PENDING).count()}
