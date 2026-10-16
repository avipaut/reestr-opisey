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
    code = models.CharField("рекомендуемый код", max_length=32)
    status = models.ForeignKey(Status, null=True, on_delete=models.PROTECT, related_name="+")
    priority = models.SmallIntegerField(null=True)
    object_type = models.ForeignKey(ObjectType, null=True, on_delete=models.PROTECT, related_name="+")
    rows = models.IntegerField(default=0)
    qty = models.DecimalField(max_digits=18, decimal_places=3, default=0)
    cost_initial = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    cost_balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
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
