"""Фоновые задачи django-q2 (обработчик очереди: manage.py qcluster).

Расписание — запись Schedule в базе, создаётся командой setup_schedule один
раз и дальше правится из админки (граф APCS: debt-celery-schedule-split,
debt-beat-admin-edits-reverted)."""

from .services import scan


def scan_task(roots=None, run_id=None):
    from .models import ScanRun
    run = scan(roots, run=ScanRun.objects.get(pk=run_id) if run_id else None)
    return {"state": run.state, "pending": run.pending, "errors": run.errors}


def upload_task(run_id):
    """Книги, перетащенные в «Импорт»: разбор и стирание."""
    from .models import ScanRun
    from .services import scan_upload
    run = scan_upload(run=ScanRun.objects.get(pk=run_id))
    return {"state": run.state, "pending": run.pending, "errors": run.errors}
