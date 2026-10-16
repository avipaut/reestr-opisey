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
        CANCELLED = "cancelled", "остановлен"

    state = models.CharField("состояние", max_length=16, choices=State.choices, default=State.RUNNING)
    roots = models.JSONField("папки", default=list)
    started_at = models.DateTimeField("начало", auto_now_add=True)
    finished_at = models.DateTimeField("конец", null=True, blank=True)
    found = models.IntegerField("файлов найдено", default=0)
    unchanged = models.IntegerField("без изменений (по размеру и времени)", default=0)
    moved = models.IntegerField("копии (то же содержимое уже есть)", default=0)
    parsed = models.IntegerField("разобрано", default=0)
    pending = models.IntegerField("ждут просмотра", default=0)
    accepted = models.IntegerField("принято", default=0)
    ignored = models.IntegerField("проигнорировано (вне «обработанные»)", default=0)
    warnings = models.IntegerField("книг с ошибками данных", default=0)
    log_file = models.CharField("файл журнала", max_length=1000, blank=True)
    same_content = models.IntegerField("строки как в актуальной", default=0)
    errors = models.IntegerField("ошибок", default=0)
    missing = models.IntegerField("пропали из папки", default=0)
    log = models.TextField("журнал", blank=True)
    cancel = models.BooleanField("остановить", default=False)
    mem_mb = models.IntegerField("память процесса, МБ", default=0)
    mem_peak_mb = models.IntegerField("память — пик, МБ", default=0)  # кнопки «Остановить обход», «Очистить базу»
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


class AcceptJob(models.Model):
    """Массовый приём описей — фоновое задание с ходом (3 739 описей по одной не принимают)."""
    started_at = models.DateTimeField("начало", auto_now_add=True)
    finished_at = models.DateTimeField("конец", null=True, blank=True)
    by = models.ForeignKey("auth.User", null=True, on_delete=models.SET_NULL, related_name="+", verbose_name="кто")
    total = models.IntegerField("всего", default=0)
    done = models.IntegerField("принято", default=0)
    failed = models.IntegerField("не принято", default=0)
    log = models.TextField("журнал", blank=True)

    class Meta:
        verbose_name = "массовый приём"
        verbose_name_plural = "массовые приёмы"
        ordering = ["-started_at"]


class ScanFile(models.Model):
    """Итог обхода по каждому файлу — для дерева файлов на странице обхода
    (пользователь 16.10.2026: «И файловую структуру в студию при импорте. Что
    проигнорировано, что считано тоже прямо в ней»)."""

    class Status(models.TextChoices):
        ACCEPTED = "accepted", "считано"
        PENDING = "pending", "ждёт просмотра"
        SAME = "same", "строки как в актуальной"
        UNCHANGED = "unchanged", "без изменений"
        COPY = "copy", "копия"
        IGNORED = "ignored", "проигнорировано (вне «обработанные»)"
        ERROR = "error", "ошибка"

    run = models.ForeignKey(ScanRun, on_delete=models.CASCADE, related_name="files")
    root = models.CharField("корень", max_length=1000)
    rel_dir = models.CharField("папка от корня", max_length=1500, blank=True)
    name = models.CharField("файл", max_length=500)
    status = models.CharField("итог", max_length=12, choices=Status.choices)
    note = models.TextField("пояснение", blank=True)
    inventory_id = models.BigIntegerField("опись", null=True, blank=True)

    class Meta:
        verbose_name = "файл обхода"
        verbose_name_plural = "файлы обхода"
        indexes = [models.Index(fields=["run", "rel_dir"], name="scanfile_run_dir")]
