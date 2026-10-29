"""Свод версии описи со сроком действия (ТЗ 4, 9.1).

Свод меняется только при приёме версии и правке статуса, поэтому
ежедневные снимки не нужны: строки свода версии живут от valid_from до
valid_to. Состояние на дату Д — строки, где valid_from ≤ Д < valid_to
(пусто — по сей день); граф APCS: debt-position-at-time-broken.
"""

from django.db import models

from apps.inventories.models import Inventory, ObjectType, Status
from apps.orgs.models import Node


class VersionStat(models.Model):
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="stats")
    node = models.ForeignKey(Node, on_delete=models.PROTECT, related_name="+")
    code = models.TextField("рекомендуемый код")
    status = models.ForeignKey(Status, null=True, on_delete=models.PROTECT, related_name="+")
    priority = models.SmallIntegerField(null=True)
    object_type = models.ForeignKey(ObjectType, null=True, on_delete=models.PROTECT, related_name="+")
    rows = models.IntegerField(default=0)
    qty = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    cost_initial = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    cost_balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    depreciation = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    # Вид строк — для флагов «учитывать» (apps.stats.flags; решение пользователя 29.10.2026:
    # «адаптировать все сборы статистики, все числа в интерфейсе под флаги»): свод хранит все
    # позиции версии, вид — ключ строки свода. Устаревшая опись — признак описи (outdated_by).
    measure = models.BooleanField("меры", default=False)
    suspect = models.BooleanField("подозрительная стоимость не подтверждена", default=False)
    dup = models.BooleanField("повтор строки другой описи", default=False)
    valid_from = models.DateTimeField()
    valid_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "свод версии"
        verbose_name_plural = "своды версий"
        indexes = [
            models.Index(fields=["valid_from", "valid_to"], name="stat_validity"),
            models.Index(fields=["node", "valid_from"], name="stat_node_from"),
            models.Index(fields=["inventory", "valid_to"], name="stat_inv_open"),
        ]


class CodeGroup(models.Model):
    """Группа кодов — сумма по кодам, начинающимся с заданного (решение
    пользователя 16.10.2026: «31122», «31123 12/13» — «чтобы коды, начинающиеся
    с этого, автоматически считались суммой. Отдельными полями. Добавь в отчёты»)."""
    name = models.CharField("название", max_length=100, unique=True)
    prefixes = models.CharField("начала кодов (через запятую)", max_length=500,
                                help_text="например: 3112312, 3112313")
    order = models.PositiveSmallIntegerField("порядок", default=100)
    is_active = models.BooleanField("в отчётах", default=True)

    class Meta:
        verbose_name = "группа кодов"
        verbose_name_plural = "группы кодов"
        ordering = ["order", "name"]

    def __str__(self):
        return self.name

    @property
    def prefix_list(self):
        return [p.strip() for p in self.prefixes.replace(";", ",").split(",") if p.strip()]

    def matches(self, code):
        return any((code or "").startswith(p) for p in self.prefix_list)


class SavedQuery(models.Model):
    """Сохранённый запрос конструктора (решение пользователя 29.10.2026: конструктор запросов
    с группировкой, отборами, выгрузкой и сохранёнными запросами). Общий для всех
    пользователей; удаляет автор или администратор."""
    name = models.CharField("название", max_length=200, unique=True)
    query = models.TextField("параметры (адрес)")
    created_by = models.ForeignKey("auth.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="кто сохранил")
    created_at = models.DateTimeField("когда", auto_now_add=True)

    class Meta:
        verbose_name = "сохранённый запрос"
        verbose_name_plural = "сохранённые запросы"
        ordering = ["name"]

    def __str__(self):
        return self.name
