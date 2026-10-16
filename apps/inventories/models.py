"""Описи и их позиции.

Опись (Inventory) — версия описи узла дерева учреждений. Найденная книга
разбирается в опись в состоянии «ждёт просмотра»; принятая становится
актуальной версией узла, прежняя уходит в историю (срок действия
valid_from — valid_to). Формат книги — запись общего графа
normalizovannaya-opis-format; ТЗ — tz-reestr-opisey (1.5–1.8, 2, 3, 4).
"""

from django.conf import settings
from django.db import models

from apps.orgs.models import Node

MONEY = {"max_digits": 18, "decimal_places": 2, "null": True, "blank": True}


class Status(models.Model):
    """Статус позиции (список): справочник самого приложения, правит администратор."""
    name = models.CharField("название", max_length=100, unique=True)
    order = models.PositiveSmallIntegerField("порядок", default=100)
    is_active = models.BooleanField("действует", default=True)

    class Meta:
        verbose_name = "статус"
        verbose_name_plural = "статусы"
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class ObjectType(models.Model):
    """«Тип объекта (определён)» — значения из описей."""
    name = models.CharField("название", max_length=300, unique=True)

    class Meta:
        verbose_name = "тип объекта"
        verbose_name_plural = "типы объекта"
        ordering = ["name"]

    def __str__(self):
        return self.name


class SourceFile(models.Model):
    """Файл в сетевой папке. Путь, размер и время изменения совпали — книга не
    открывается; иначе — SHA-256, и только новое содержимое разбирается (ТЗ 2)."""
    path = models.CharField("путь", max_length=2000, unique=True)
    root = models.CharField("корневая папка", max_length=1000)
    rel_folder = models.CharField("папка от корня", max_length=1500)
    name = models.CharField("имя файла", max_length=500)
    size = models.BigIntegerField("размер")
    mtime = models.DateTimeField("изменён")
    sha256 = models.CharField("SHA-256", max_length=64, db_index=True)
    first_seen = models.DateTimeField("впервые виден", auto_now_add=True)
    last_seen = models.DateTimeField("последний раз виден")
    missing = models.BooleanField("нет в папке", default=False)

    class Meta:
        verbose_name = "файл"
        verbose_name_plural = "файлы"

    def __str__(self):
        return self.path


class Inventory(models.Model):
    class State(models.TextChoices):
        PENDING = "pending", "ждёт просмотра"
        ACCEPTED = "accepted", "принята"
        REJECTED = "rejected", "отклонена"
        UNCHANGED = "unchanged", "без изменений"
        ERROR = "error", "ошибка разбора"

    state = models.CharField("состояние", max_length=16, choices=State.choices, db_index=True)
    node = models.ForeignKey(Node, null=True, blank=True, on_delete=models.PROTECT,
                             related_name="inventories", verbose_name="узел")
    # Узел по пути папки, если его ещё нет в дереве: [{code, name, folder}] — создаётся при приёме.
    proposed_path = models.JSONField("узел по пути папки", default=list, blank=True)
    node_resolved = models.BooleanField("узел определён", default=False)
    source_file = models.ForeignKey(SourceFile, on_delete=models.PROTECT, related_name="inventories",
                                    verbose_name="файл")
    sha256 = models.CharField("SHA-256 файла", max_length=64, db_index=True)
    content_hash = models.CharField("хеш содержимого", max_length=64, db_index=True, blank=True)
    error = models.TextField("ошибка", blank=True)

    parsed_at = models.DateTimeField("разобрана", auto_now_add=True)
    decided_at = models.DateTimeField("решение", null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="кто решил")
    # Срок действия версии; дата версии — дата загрузки в базу (принятия), ТЗ 4.1.1.
    valid_from = models.DateTimeField("действует с", null=True, blank=True, db_index=True)
    valid_to = models.DateTimeField("действует по", null=True, blank=True, db_index=True)
    is_current = models.BooleanField("актуальная", default=False)

    # Сведения об описи (лист «Сводка», имя файла, «Доп. сведения источника»).
    org_text = models.CharField("организация (как в описи)", max_length=1000, blank=True)
    source_date = models.DateField("дата источника", null=True, blank=True)
    source_name = models.CharField("исходный файл", max_length=1000, blank=True)
    source_sheet = models.CharField("лист источника", max_length=300, blank=True)
    source_kind = models.CharField("вид источника", max_length=300, blank=True)
    source_hierarchy = models.CharField("иерархия источника", max_length=300, blank=True)
    source_folder = models.CharField("папка источника", max_length=1000, blank=True)
    cost_basis = models.CharField("основа стоимостей", max_length=300, blank=True)
    check_flag = models.BooleanField("пометка «ПРОВЕРИТЬ»", default=False)
    summary_lines = models.JSONField("строки «Сводки»", default=list, blank=True)
    unknown_columns = models.JSONField("столбцы вне схемы", default=list, blank=True)

    rows = models.IntegerField("строк", default=0)
    qty = models.DecimalField("количество", max_digits=18, decimal_places=3, default=0)
    cost_initial = models.DecimalField("первонач. стоимость", max_digits=20, decimal_places=2, default=0)
    cost_balance = models.DecimalField("балансовая стоимость", max_digits=20, decimal_places=2, default=0)

    class Meta:
        verbose_name = "опись"
        verbose_name_plural = "описи"
        constraints = [
            models.UniqueConstraint(fields=["node"], condition=models.Q(is_current=True),
                                    name="one_current_inventory_per_node"),
        ]

    def __str__(self):
        return f"{self.source_file.name} ({self.get_state_display()})"


class Item(models.Model):
    """Позиция описи — все столбцы листа своими полями (ТЗ 3.3)."""
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="items", verbose_name="опись")
    sheet = models.CharField("лист", max_length=100)
    row_no = models.IntegerField("строка листа")

    name = models.TextField("основное средство", blank=True)
    account_code = models.CharField("код учёта", max_length=32, blank=True)
    qty = models.DecimalField("количество", max_digits=14, decimal_places=3, default=1)
    qty_note = models.CharField("количество — пометка", max_length=40, blank=True)
    inv_no = models.CharField("инв. номер", max_length=200, blank=True)
    inv_key = models.CharField("ключ позиции", max_length=200, blank=True, db_index=True)
    cost_initial = models.DecimalField("первонач. стоимость", **MONEY)
    depreciation = models.DecimalField("накоп. износ", **MONEY)
    cost_balance = models.DecimalField("балансовая стоимость", **MONEY)
    priority = models.SmallIntegerField("приоритет", null=True, blank=True)
    mol = models.CharField("МОЛ", max_length=500, blank=True)
    recommended_code = models.CharField("рекомендуемый код", max_length=32, blank=True, db_index=True)
    department_text = models.CharField("ведомство (как в описи)", max_length=1000, blank=True)
    suborg_text = models.CharField("подведомство (как в описи)", max_length=1000, blank=True)
    model = models.CharField("модель", max_length=500, blank=True)
    working = models.CharField("рабочее или нет", max_length=200, blank=True)
    # Статус: действующий (меняет администратор, ТЗ 4.2) и как в описи.
    status = models.ForeignKey(Status, null=True, on_delete=models.PROTECT, related_name="items",
                               verbose_name="статус")
    status_doc = models.CharField("статус по описи", max_length=100, blank=True)
    reason = models.TextField("причина", blank=True)
    comment = models.TextField("комментарий", blank=True)
    object_type = models.ForeignKey(ObjectType, null=True, blank=True, on_delete=models.PROTECT,
                                    verbose_name="тип объекта")
    fully_depreciated = models.BooleanField("полностью амортизирован", null=True, blank=True)
    subdivision = models.CharField("подразделение", max_length=500, blank=True)
    org_code = models.CharField("код организации (42→31)", max_length=32, blank=True)
    source_row = models.IntegerField("строка источника", null=True, blank=True)
    saldo_begin_initial = models.DecimalField("сальдо нач.: первонач.", **MONEY)
    saldo_begin_dep = models.DecimalField("сальдо нач.: износ", **MONEY)
    saldo_begin_balance = models.DecimalField("сальдо нач.: балансовая", **MONEY)
    income_initial = models.DecimalField("приход: первонач.", **MONEY)
    income_dep = models.DecimalField("приход: износ", **MONEY)
    expense_initial = models.DecimalField("расход: первонач.", **MONEY)
    expense_dep = models.DecimalField("расход: износ", **MONEY)
    source_extra = models.TextField("доп. сведения источника", blank=True)
    user_mark = models.TextField("отметка пользователя", blank=True)
    user_mark_date = models.DateField("дата отметки (из шапки)", null=True, blank=True)
    name_fill = models.CharField("заливка наименования", max_length=8, blank=True)
    row_hash = models.CharField("хеш строки", max_length=32)
    extra = models.JSONField("столбцы вне схемы", default=dict, blank=True)

    class Meta:
        verbose_name = "позиция"
        verbose_name_plural = "позиции"
        indexes = [models.Index(fields=["inventory", "sheet", "row_no"], name="item_inv_sheet_row")]

    def __str__(self):
        return self.name[:80]


class ItemStatusChange(models.Model):
    """Правка статуса администратором (ТЗ 4.2): было, стало, кто, когда."""
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="status_changes", verbose_name="позиция")
    old = models.ForeignKey(Status, null=True, on_delete=models.PROTECT, related_name="+", verbose_name="было")
    new = models.ForeignKey(Status, on_delete=models.PROTECT, related_name="+", verbose_name="стало")
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+",
                           verbose_name="кто")
    at = models.DateTimeField("когда", auto_now_add=True)
    comment = models.TextField("комментарий", blank=True)

    class Meta:
        verbose_name = "правка статуса"
        verbose_name_plural = "правки статусов"


class Reconciliation(models.Model):
    """«Сводка»: сверка с итогами источника по счёту."""
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="reconciliations")
    account = models.CharField("счёт", max_length=200)
    src_initial = models.DecimalField("итог источника: первонач.", **MONEY)
    sum_initial = models.DecimalField("сумма позиций: первонач.", **MONEY)
    src_dep = models.DecimalField("итог источника: износ", **MONEY)
    sum_dep = models.DecimalField("сумма позиций: износ", **MONEY)
    matches = models.CharField("совпадает", max_length=300, blank=True)

    class Meta:
        verbose_name = "сверка с источником"
        verbose_name_plural = "сверки с источником"


class Rule(models.Model):
    """«Правила» описи: как заполнен каждый столбец — хранятся при каждой версии."""
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="rules")
    order = models.PositiveIntegerField("порядок")
    field = models.CharField("поле", max_length=300)
    text = models.TextField("как заполнено")

    class Meta:
        verbose_name = "правило описи"
        verbose_name_plural = "правила описи"
        ordering = ["inventory", "order"]
