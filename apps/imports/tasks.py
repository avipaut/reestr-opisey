"""Фоновые задачи django-q2 (обработчик очереди: manage.py qcluster).

Расписание — запись Schedule в базе, создаётся командой setup_schedule один
раз и дальше правится из админки (граф APCS: debt-celery-schedule-split,
debt-beat-admin-edits-reverted)."""

from .services import scan


def scan_task():
    run = scan()
    return {"state": run.state, "pending": run.pending, "errors": run.errors}
