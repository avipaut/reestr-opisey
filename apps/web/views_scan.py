"""Страница обхода: ход без перезагрузки страницы и дерево файлов с итогом
по каждому — считано, без изменений, копия, проигнорировано, ошибка
(пользователь 16.10.2026: «И файловую структуру в студию при импорте. Что
проигнорировано, что считано тоже прямо в ней»)."""

from collections import Counter, defaultdict

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from apps.imports.models import ScanFile, ScanRun

STATUSES = [(s.value, s.label) for s in ScanFile.Status]
FIELDS = ["state", "stage", "current", "found", "ignored", "unchanged", "moved", "parsed", "to_parse", "accepted",
          "pending", "same_content", "errors", "warnings", "mem_mb", "mem_peak_mb"]


@login_required
def scan_progress(request, pk):
    run = get_object_or_404(ScanRun, pk=pk)
    d = {f: getattr(run, f) for f in FIELDS}
    d.update(finished=bool(run.finished_at), state_label=run.get_state_display())
    return JsonResponse(d)


def _split(rel):
    return [p for p in rel.replace("/", "\\").split("\\") if p]


@login_required
def scan_tree(request, pk):
    """Дети папки dir корня root: папки со счётчиками по поддереву и файлы с итогом."""
    run = get_object_or_404(ScanRun, pk=pk)
    roots = list(run.roots or []) or list(run.files.values_list("root", flat=True).distinct())
    ri = request.GET.get("root")
    if ri is None or not ri.isdigit() or int(ri) >= len(roots):
        # верхний уровень — корни выбранных папок
        out = []
        for i, r in enumerate(roots):
            c = Counter({x["status"]: x["n"] for x in run.files.filter(root=r).values("status").annotate(n=Count("id"))})
            out.append({"name": r, "root": i, "dir": "", "counts": c})
        return JsonResponse({"dirs": out, "files": []})
    root = roots[int(ri)]
    cur = _split(request.GET.get("dir") or "")
    rows = run.files.filter(root=root)
    # счётчики по папкам — одним запросом, сложение по поддереву — здесь
    per_dir = defaultdict(Counter)
    for x in rows.values("rel_dir", "status").annotate(n=Count("id")):
        rel, status, n = x["rel_dir"], x["status"], x["n"]
        parts = _split(rel)
        if parts[:len(cur)] == cur and len(parts) > len(cur):
            per_dir[parts[len(cur)]][status] += n
    dirs = [{"name": name, "root": int(ri), "dir": "\\".join(cur + [name]), "counts": per_dir[name]}
            for name in sorted(per_dir, key=str.lower)]
    files = [{"name": f.name, "status": f.status, "label": f.get_status_display(), "note": f.note,
              "inventory": f.inventory_id}
             for f in rows.filter(rel_dir="\\".join(cur)).order_by("name")]
    return JsonResponse({"dirs": dirs, "files": files})


@login_required
def scan_export(request, pk):
    """«Выгрузить в Excel» — ошибки с путями, ошибки данных, пропущенные, исключённые позиции."""
    from urllib.parse import quote

    from django.http import HttpResponse

    from apps.imports.report import build
    run = get_object_or_404(ScanRun, pk=pk)
    data, _ = build(run)
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f'Обход {run.pk} — ошибки и пропущенные.xlsx')}"
    return resp
