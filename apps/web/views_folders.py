"""Импорт папки: выбранная папка обходится рекурсивно (решение пользователя
16.10.2026: «Выбираем мы папку, откуда забираем описи рекурсивным обходом»).

Подпапки первого уровня выбранной папки — учреждения, ниже — подведы
(ТЗ 1.4). Папка — на сервере или в сети (путь, обзор по папкам сервера).
Файлы читаются на месте и к себе не копируются (решение пользователя
16.10.2026: «И мы не копируем файлы к себе!»): в базу идут разобранные
сведения, путь к книге и её хеш.
"""

import os
import string

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.imports import services as imports
from apps.imports.models import ScanRun

EXTS = tuple(settings.INVENTORY_EXTENSIONS)


def _start_scan(request, root):
    """Обход в фоне: на сервере — обработчик очереди, в разработке — поток."""
    run = ScanRun.objects.create(roots=[root], stage="в очереди")
    if getattr(settings, "SCAN_SYNC", False):  # тесты
        imports.scan([root], run=run)
    elif settings.DEBUG:  # разработка: фоновый поток того же процесса, без обработчика очереди
        import threading
        from django.db import connection

        def work():
            try:
                imports.scan([root], run=run)
            finally:
                connection.close()

        threading.Thread(target=work, daemon=True).start()
    else:
        from django_q.tasks import async_task
        async_task("apps.imports.tasks.scan_task", [root], run.pk)
    return run


def recent_folders(limit=8):
    seen, out = set(), []
    for roots in ScanRun.objects.order_by("-started_at").values_list("roots", flat=True)[:200]:
        for r in roots or []:
            key = os.path.normcase(r)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    return out[:limit]


@login_required
@permission_required("inventories.add_inventory", raise_exception=True)
def fs_list(request):
    """Папки сервера для обзора: без пути — диски и недавние; иначе — подпапки
    и сколько книг прямо в папке. Только чтение имён, файлы не открываются.
    ДЛЯ СЕРВЕРНОЙ ВЕРСИИ: обзор показывает файловую систему сервера — право
    у тех, кто загружает описи; при необходимости ограничить разрешёнными
    корнями (настройка) — решение при установке."""
    path = (request.GET.get("path") or "").strip()
    if not path:
        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")] if os.name == "nt" else ["/"]
        return JsonResponse({"path": "", "parent": None, "dirs": [{"name": d, "path": d} for d in drives],
                             "recent": recent_folders(), "books": 0})
    path = os.path.normpath(path)
    if not os.path.isdir(path):
        return JsonResponse({"error": f"папка не найдена или нет доступа: {path}"}, status=404)
    dirs, books = [], 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir():
                        if not e.name.startswith(("$", ".")):
                            dirs.append({"name": e.name, "path": e.path})
                    elif e.name.lower().endswith(EXTS) and not e.name.startswith("~$"):
                        books += 1
                except OSError:
                    continue
    except OSError as e:
        return JsonResponse({"error": f"нет доступа: {e.strerror or e}"}, status=403)
    dirs.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(path.rstrip("\\/"))
    return JsonResponse({"path": path, "parent": parent if parent and parent != path else "",
                         "dirs": dirs, "books": books})


@login_required
@permission_required("inventories.add_inventory", raise_exception=True)
@require_POST
def import_folder(request):
    path = os.path.normpath((request.POST.get("path") or "").strip()) if request.POST.get("path") else ""
    if not path or not os.path.isdir(path):
        messages.error(request, f"Папка не найдена или нет доступа: {path or '—'}")
        return redirect("import_page")
    run = _start_scan(request, path)
    messages.info(request, f"Обход папки {path} начат — ход ниже; найденные описи появятся «На просмотр».")
    return redirect("scan_detail", pk=run.pk)
