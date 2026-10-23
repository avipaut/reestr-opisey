"""Число описей на просмотр — в меню каждого экрана."""

from apps.inventories.models import Inventory


def nav(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    # here — адрес этой страницы для «← к результатам» из карточки позиции; собран из
    # разобранных параметров: get_full_path портит кириллицу, пришедшую без %-кодов.
    here = request.path + ("?" + request.GET.urlencode() if request.GET else "")
    return {"pending_count": Inventory.objects.filter(state=Inventory.State.PENDING).count(), "here": here}
