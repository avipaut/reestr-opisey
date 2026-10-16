"""Журнал обходов папок — одна таблица, в неё пишет сам обход
(граф APCS: debt-import-history-dead). Итог — честные числа и ошибки
(граф APCS: debt-import-swallows-failures)."""

from django.db import models


class ScanRun(models.Model):
    class State(models.TextChoices):
        RUNNING = "running", "идёт"
        DONE = "done", "готово"
        DONE_ERRORS = "done_errors", "готово с ошибками"
        FAILED = "failed", "прервано ошибкой"
        BUSY = "busy", "пропущено: идёт другой обход"

    state = models.CharField("состояние", max_length=16, choices=State.choices, default=State.RUNNING)
    roots = models.JSONField("папки", default=list)
    started_at = models.DateTimeField("начало", auto_now_add=True)
    finished_at = models.DateTimeField("конец", null=True, blank=True)
    found = models.IntegerField("файлов найдено", default=0)
    unchanged = models.IntegerField("без изменений (по размеру и времени)", default=0)
    moved = models.IntegerField("копии (то же содержимое уже есть)", default=0)
    parsed = models.IntegerField("разобрано", default=0)
    pending = models.IntegerField("ждут просмотра", default=0)
    same_content = models.IntegerField("строки как в актуальной", default=0)
    errors = models.IntegerField("ошибок", default=0)
    missing = models.IntegerField("пропали из папки", default=0)
    log = models.TextField("журнал", blank=True)
    # Ход обхода — для экрана, пока обход идёт (обновляется раз в несколько файлов).
    stage = models.CharField("этап", max_length=40, blank=True)
    to_parse = models.IntegerField("к разбору", default=0)
    current = models.CharField("текущий файл", max_length=1000, blank=True)

    class Meta:
        verbose_name = "обход папок"
        verbose_name_plural = "обходы папок"
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.started_at:%d.%m.%Y %H:%M} — {self.get_state_display()}"

    @property
    def duration(self):
        return (self.finished_at - self.started_at).total_seconds() if self.finished_at else None
