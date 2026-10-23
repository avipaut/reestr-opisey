"""Дерево учреждений: учреждение → подвед → подвед подведа…

Решение пользователя 16.10.2026: «Мы храним учреждения и подведы как коды +
наименования если они есть. Верхний уровень папки - учреждение. Внутри -
подвед. еще ниже подвед подведа и т.д.»; «126 - номер учреждения 8 - номер
подведа. Ошское ДНВТ - имя». ТЗ — запись общего графа tz-reestr-opisey (1.4, 1.8, 3).
"""

from django.conf import settings
from django.db import models


class Node(models.Model):
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT,
                               related_name="children", verbose_name="родитель")
    code = models.CharField("код", max_length=32, blank=True)
    name = models.CharField("наименование", max_length=500, blank=True)
    level = models.PositiveSmallIntegerField("уровень", default=0, editable=False)
    # Путь кодов от учреждения: «/126/8/» — поддерево отбирается по началу пути.
    path = models.CharField("путь кодов", max_length=500, editable=False, db_index=True)
    search = models.CharField("строка поиска", max_length=600, blank=True, editable=False)
    created_at = models.DateTimeField("создан", auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+", verbose_name="кем создан")

    class Meta:
        verbose_name = "узел дерева учреждений"
        verbose_name_plural = "дерево учреждений"
        ordering = ["path"]
        constraints = [
            # Код уникален среди детей одного родителя (у корня — среди учреждений).
            models.UniqueConstraint(fields=["parent", "code"], condition=~models.Q(code=""),
                                    name="node_code_unique_in_parent"),
            models.UniqueConstraint(fields=["code"], condition=models.Q(parent__isnull=True) & ~models.Q(code=""),
                                    name="node_root_code_unique"),
        ]
        indexes = [models.Index(fields=["path"], name="node_path_prefix",
                                opclasses=["varchar_pattern_ops"])]

    def __str__(self):
        return self.label

    @property
    def label(self):
        if self.code and self.name:
            return f"{self.code}. {self.name}"
        return self.code or self.name or f"узел {self.pk}"

    @property
    def trail(self):
        """«126 › 8. Ошское ДНВТ» — цепочка от учреждения. Для списков —
        fill_trails: иначе каждый узел достаёт своих предков по одному."""
        if getattr(self, "_trail", None) is None:
            parts, n = [], self
            while n is not None:
                parts.append(n.label)
                n = n.parent
            self._trail = " › ".join(reversed(parts))
        return self._trail

    @classmethod
    def fill_trails(cls, nodes):
        """Цепочки для многих узлов одним запросом: предки — по началам пути «/126/8/»."""
        nodes = list(nodes)
        todo = [n for n in nodes if n is not None and getattr(n, "_trail", None) is None]
        prefixes = set()
        for n in todo:
            parts = n.path.strip("/").split("/")
            prefixes.update("/" + "/".join(parts[:i]) + "/" for i in range(1, len(parts)))
        by_path = {a.path: a.label for a in cls.objects.filter(path__in=prefixes)} if prefixes else {}
        for n in todo:
            parts = n.path.strip("/").split("/")
            chain =["/" + "/".join(parts[:i]) + "/" for i in range(1, len(parts))]
            if all(p in by_path for p in chain):
                n._trail = " › ".join([by_path[p] for p in chain] + [n.label])
        return nodes

    def save(self, *args, **kwargs):
        self._trail = None
        self.search =" ".join(x for x in (self.code, self.name) if x).lower().replace("ё", "е")
        part = self.code or f"#{self.pk or 'new'}"
        if self.parent_id:
            self.level = self.parent.level + 1
            self.path = f"{self.parent.path}{part}/"
        else:
            self.level = 0
            self.path = f"/{part}/"
        super().save(*args, **kwargs)
        if not self.code and "#new" in self.path:  # путь узла без кода — по его номеру
            self.path = self.path.replace("#new", f"#{self.pk}")
            super().save(update_fields=["path"])


class FolderMapping(models.Model):
    """Папка без кода или служебная → узел, выбранный пользователем в просмотре.
    Запоминается для следующих описей той же папки (ТЗ 1.8)."""
    folder = models.CharField("папка (относительный путь)", max_length=1000, unique=True)
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="folder_mappings", verbose_name="узел")
    created_at = models.DateTimeField("создано", auto_now_add=True)

    class Meta:
        verbose_name = "соответствие папки узлу"
        verbose_name_plural = "соответствия папок узлам"

    def __str__(self):
        return f"{self.folder} → {self.node}"
