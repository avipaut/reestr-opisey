"""Импорт папки: выбранная папка обходится рекурсивно (решение пользователя
16.10.2026: «Выбираем мы папку, откуда забираем описи рекурсивным обходом»).

Подпапки первого уровня выбранной папки — учреждения, ниже — подведы
(ТЗ 1.4). Папка — на сервере или в сети (путь, обзор по папкам сервера).
Файлы читаются на месте и к себе не копируются (решение пользователя
16.10.2026: «И мы не копируем файлы к себе!»): в базу идут разобранные
сведения, путь к книге и её хеш.
"""

import os
import re
import string

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.imports import services as imports
from apps.imports.models import ScanRun

EXTS = tuple(settings.INVENTORY_EXTENSIONS)


def _start_scan(request, root, upload=False):
    """Обход в фоне: на сервере — обработчик очереди, в разработке — поток.
    upload — книги, перетащенные с компьютера: после разбора стираются."""
    run = ScanRun.objects.create(roots=[root], stage="в очереди")
    work_now = (lambda: imports.scan_upload(run=run)) if upload else (lambda: imports.scan([root], run=run))
    if getattr(settings, "SCAN_SYNC", False):  # тесты
        work_now()
    elif settings.DEBUG:  # разработка: фоновый поток того же процесса, без обработчика очереди
        import threading
        from django.db import connection

        def work():
            try:
                work_now()
            finally:
                connection.close()

        threading.Thread(target=work, daemon=True).start()
    else:
        from django_q.tasks import async_task
        if upload:
            async_task("apps.imports.tasks.upload_task", run.pk)
        else:
            async_task("apps.imports.tasks.scan_task", [root], run.pk)
    return run


def recent_folders(limit=8):
    seen, out = {os.path.normcase(imports.upload_root())}, []  # папка загрузок — не для выбора
    for roots in ScanRun.objects.order_by("-started_at").values_list("roots", flat=True)[:200]:
        for r in roots or []:
            key = os.path.normcase(os.path.normpath(r))
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


# ------------------------------------------------------------------ перетаскивание с компьютера
# Браузер не сообщает пути папки на диске — только сами файлы и пути внутри
# перетащенного. Поэтому книги загружаются (пачками, с ходом), разбираются тем же
# обходом и стираются (решение пользователя 19.10.2026 «Разобрать и стереть»).

def _upload_target(rel):
    """Путь внутри папки загрузок по пути из браузера; None — не книга или путь чужой."""
    parts = [p.strip() for p in re.split(r"[\\/]+", rel or "") if p.strip() not in ("", ".")]
    if not parts or any(p == ".." or ":" in p for p in parts):
        return None
    if parts[-1].startswith("~$") or not parts[-1].lower().endswith(EXTS):
        return None
    root = os.path.abspath(imports.upload_root())
    dest = os.path.abspath(os.path.join(root, *parts))
    return dest if os.path.commonpath([root, dest]) == root else None


@login_required
@permission_required("inventories.add_inventory", raise_exception=True)
@require_POST
def upload_files(request):
    """Пачка книг: files[] и paths[] (путь внутри перетащенного) — в папку загрузок.
    first=1 — начало загрузки: остатки прерванной прежней стираются."""
    if imports.scan_busy():
        return JsonResponse({"error": "идёт обход — дождитесь конца или остановите его"}, status=409)
    if request.POST.get("first") == "1":
        imports.clear_upload()
    files, paths = request.FILES.getlist("files"), request.POST.getlist("paths")
    if len(files) != len(paths):
        return JsonResponse({"error": "файлы и пути не совпадают"}, status=400)
    saved, skipped = 0, []
    for f, rel in zip(files, paths):
        dest = _upload_target(rel)
        if dest is None:
            skipped.append(rel)
            continue
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)
            saved += 1
        except OSError as e:
            return JsonResponse({"error": f"не записан {rel}: {e.strerror or e}"}, status=500)
    return JsonResponse({"saved": saved, "skipped": skipped})


@login_required
@permission_required("inventories.add_inventory", raise_exception=True)
@require_POST
def upload_start(request):
    """Загрузка закончена — разбор в фоне; ответ — адрес страницы обхода."""
    if imports.scan_busy():
        return JsonResponse({"error": "идёт обход — дождитесь конца или остановите его"}, status=409)
    root = imports.upload_root()
    if not any(files for _, _, files in os.walk(root)):
        return JsonResponse({"error": "книг для разбора нет — загрузите заново"}, status=400)
    run = _start_scan(request, root, upload=True)
    messages.info(request, "Книги загружены — идёт разбор; после него загруженные файлы стираются.")
    return JsonResponse({"url": reverse("scan_detail", args=[run.pk])})


@login_required
@permission_required("inventories.add_inventory", raise_exception=True)
@require_POST
def scan_stop(request):
    n = ScanRun.objects.filter(finished_at__isnull=True).update(cancel=True)
    messages.info(request, "Обход останавливается — дочитывается текущая книга" if n else "Идущих обходов нет")
    pk = request.POST.get("run")
    return redirect("scan_detail", pk=int(pk)) if pk and pk.isdigit() else redirect("import_page")


@login_required
@require_POST
def wipe(request):
    """«Очистить базу» — только администратор и только со словом ОЧИСТИТЬ."""
    if not request.user.is_superuser:
        messages.error(request, "Очистить базу может только администратор")
        return redirect("import_page")
    if (request.POST.get("confirm") or "").strip().upper() != "ОЧИСТИТЬ":
        messages.error(request, "Не очищено: впишите слово ОЧИСТИТЬ")
        return redirect("import_page")
    stopped = imports.wipe_all()
    messages.success(request, "База очищена: описи, позиции, учреждения, обходы и журналы удалены"
                     + ("" if stopped else " (обход не успел остановиться — проверьте журнал обходов)"))
    return redirect("import_page")

