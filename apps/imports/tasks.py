"""Фоновые задачи django-q2 (обработчик очереди: manage.py qcluster).

Расписание — запись Schedule в базе, создаётся командой setup_schedule один
раз и дальше правится из админки (граф APCS: debt-celery-schedule-split,
debt-beat-admin-edits-reverted)."""

from .services import scan


def scan_task(roots=None, run_id=None):
    from .models import ScanRun
    run = scan(roots, run=ScanRun.objects.get(pk=run_id) if run_id else None)
    return {"state": run.state, "pending": run.pending, "errors": run.errors}
