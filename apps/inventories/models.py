"""Описи и их позиции.

Опись (Inventory) — версия описи узла дерева учреждений. Найденная книга
разбирается в опись в состоянии «ждёт просмотра»; принятая становится
актуальной версией узла, прежняя уходит в историю (срок действия
valid_from — valid_to). Формат книги — запись общего графа
normalizovannaya-opis-format; ТЗ — tz-reestr-opisey (1.5–1.8, 2, 3, 4).
"""

from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.db import models

from apps.orgs.models import Node

MONEY = {"max_digits": 18, "decimal_places": 2, "null": True, "blank": True}


class Status(models.Model):
    """Статус позиции (список): справочник самого приложения, правит администратор."""
    name = models.TextField("название", unique=True)
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
    name = models.TextField("название", unique=True)

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
    class Via(models.TextChoices):
        FOLDER = "folder", "обход папки"
        UPLOAD = "upload", "загрузка с компьютера"

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

    parsed_at = models.DateTimeField("загружена", auto_now_add=True)
    # Откуда загружена — снимок на момент загрузки: файл потом меняется и переименовывается
    # (модуль нормализации пересобирает описи), загруженное с компьютера стирается.
    loaded_via = models.CharField("как загружена", max_length=8, choices=Via.choices, blank=True)
    loaded_from = models.CharField("откуда загружена", max_length=2000, blank=True,
                                   help_text="путь файла; для загрузки с компьютера — путь внутри перетащенного")
    loaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="+", verbose_name="кто загрузил")
    scan_run = models.ForeignKey("imports.ScanRun", null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="inventories", verbose_name="обход")
    file_size = models.BigIntegerField("размер файла", null=True, blank=True)
    file_mtime = models.DateTimeField("файл изменён", null=True, blank=True)
    decided_at = models.DateTimeField("решение", null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+", verbose_name="кто решил")
    # Срок действия версии; дата версии — дата загрузки в базу (принятия), ТЗ 4.1.1.
    valid_from = models.DateTimeField("действует с", null=True, blank=True, db_index=True)
    valid_to = models.DateTimeField("действует по", null=True, blank=True, db_index=True)
    is_current = models.BooleanField("актуальная", default=False)

    # Сведения об описи (лист «Сводка», имя файла, «Доп. сведения источника»).
    org_text = models.TextField("организация (как в описи)", blank=True)
    source_date = models.DateField("дата источника", null=True, blank=True)
    source_name = models.TextField("исходный файл", blank=True)
    source_sheet = models.TextField("лист источника", blank=True)
    source_kind = models.TextField("вид источника", blank=True)
    source_hierarchy = models.TextField("иерархия источника", blank=True)
    source_folder = models.TextField("папка источника", blank=True)
    cost_basis = models.TextField("основа стоимостей", blank=True)
    check_flag = models.BooleanField("пометка «ПРОВЕРИТЬ»", default=False)
    summary_lines = models.JSONField("строки «Сводки»", default=list, blank=True)
    unknown_columns = models.JSONField("столбцы вне схемы", default=list, blank=True)

    rows = models.IntegerField("строк", default=0)
    qty = models.DecimalField("количество", max_digits=18, decimal_places=3, default=0)
    cost_initial = models.DecimalField("первонач. стоимость", max_digits=20, decimal_places=2, default=0)
    cost_balance = models.DecimalField("балансовая стоимость", max_digits=20, decimal_places=2, default=0)
    # Итоги описи (stats.services.fill_totals): количество и деньги — без мер, как в своде.
    depreciation = models.DecimalField("накоп. износ", max_digits=20, decimal_places=2, default=0)
    rows_excluded = models.IntegerField("исключено из свода (меры)", default=0)
    rows_held = models.IntegerField("отложено до проверки (подозрительные)", default=0)
    rows_no_code = models.IntegerField("без рекомендуемого кода", default=0)
    codes = models.IntegerField("разных кодов учёта", default=0)
    # Дубли описей (apps.imports.dedup): группа похожих описей того же узла и сколько строк
    # этой описи — повторы строк других описей группы (в свод не входят).
    dup_group = models.ForeignKey("DupGroup", null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name="inventories", verbose_name="группа похожих описей")
    rows_dup = models.IntegerField("повторы строк других описей (не в своде)", default=0)

    class Meta:
        verbose_name = "опись"
        verbose_name_plural = "описи"
        constraints = [
            # Актуальная версия — одна на книгу (путь); у узла их может быть несколько.
            models.UniqueConstraint(fields=["source_file"], condition=models.Q(is_current=True),
                                    name="one_current_inventory_per_file"),
        ]

    def __str__(self):
        return f"{self.source_file.name} ({self.get_state_display()})"

    @property
    def loaded_where(self):
        """Откуда загружена — для экрана."""
        if self.loaded_via == self.Via.UPLOAD:
            return f"с компьютера: {self.loaded_from}"
        return self.loaded_from or self.source_file.path


class DupGroup(models.Model):
    """Группа похожих описей одного узла (apps.imports.dedup): описи связаны, если строки
    меньшей почти целиком есть в большей. Строки группы объединяются: повтор строки
    другой описи в свод не входит. Группы пересобираются вторым этапом каждого обхода."""
    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="dup_groups", verbose_name="узел")
    base = models.ForeignKey(Inventory, null=True, on_delete=models.SET_NULL, related_name="+",
                             verbose_name="основа (больше всего строк)")
    made_at = models.DateTimeField("собрана", auto_now_add=True)
    rows_before = models.IntegerField("строк во всех описях", default=0)
    rows_after = models.IntegerField("строк после объединения", default=0)
    cost_before = models.DecimalField("первонач. стоимость во всех описях", max_digits=20, decimal_places=2, default=0)
    cost_after = models.DecimalField("первонач. стоимость после объединения", max_digits=20, decimal_places=2, default=0)

    class Meta:
        verbose_name = "группа похожих описей"
        verbose_name_plural = "группы похожих описей"


class Item(models.Model):
    """Позиция описи — все столбцы листа своими полями (ТЗ 3.3)."""
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="items", verbose_name="опись")
    sheet = models.TextField("лист")
    row_no = models.IntegerField("строка листа")

    name = models.TextField("основное средство", blank=True)
    account_code = models.TextField("код учёта", blank=True)
    qty = models.DecimalField("количество", max_digits=14, decimal_places=3, default=1)
    qty_note = models.TextField("количество — пометка", blank=True)
    # Материалы и запасы (меры: литры, тонны, м²) в свод штук не входят — решение
    # пользователя 16.10.2026 «Исключаем». Позиция хранится и видна в поиске.
    excluded = models.BooleanField("исключено из свода", default=False)
    exclude_reason = models.TextField("почему исключено", blank=True)
    # Подозрительная первонач. стоимость (apps.imports.suspects): причина; пока не подтверждена
    # на вкладке «Подозрительные» («Верно») — held: в своды, итоги и количество не входит.
    suspect = models.CharField("подозрительная стоимость", max_length=8, blank=True, db_index=True)
    held = models.BooleanField("отложена до проверки", default=False)
    suspect_ok_at = models.DateTimeField("стоимость подтверждена", null=True, blank=True)
    suspect_ok_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="+", verbose_name="кто подтвердил")
    # Повтор строки другой описи той же группы похожих описей (apps.imports.dedup): строка,
    # которую повторяет, и ступень сопоставления; в свод, итоги и количество не входит.
    # «Сверить» — тот же инв. номер есть в группе с другой стоимостью или количеством.
    dup_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                               verbose_name="повтор строки")
    dup_step = models.SmallIntegerField("ступень сопоставления", default=0,
                                        help_text="1 — инв. номер, стоимость, количество; 2 — наименование, стоимость, количество")
    dup_check = models.CharField("сверить с группой", max_length=8, blank=True,
                                 help_text="cost — тот же инв. номер с другой стоимостью; qty — с другим количеством")
    inv_no = models.TextField("инв. номер", blank=True)
    inv_key = models.TextField("ключ позиции", blank=True, db_index=True)
    cost_initial = models.DecimalField("первонач. стоимость", **MONEY)
    # В ячейке не число (буквы) — стоимость пуста, текст ячейки — здесь (вкладка «Подозрительные»).
    cost_initial_raw = models.TextField("первонач. стоимость — текст из описи", blank=True)
    depreciation = models.DecimalField("накоп. износ", **MONEY)
    cost_balance = models.DecimalField("балансовая стоимость", **MONEY)
    priority = models.SmallIntegerField("приоритет", null=True, blank=True)
    mol = models.TextField("МОЛ", blank=True)
    recommended_code = models.TextField("рекомендуемый код", blank=True, db_index=True)
    department_text = models.TextField("ведомство (как в описи)", blank=True)
    suborg_text = models.TextField("подведомство (как в описи)", blank=True)
    model = models.TextField("модель", blank=True)
    working = models.TextField("рабочее или нет", blank=True)
    # Статус: действующий (меняет администратор, ТЗ 4.2) и как в описи.
    status = models.ForeignKey(Status, null=True, on_delete=models.PROTECT, related_name="items",
                               verbose_name="статус")
    status_doc = models.TextField("статус по описи", blank=True)
    reason = models.TextField("причина", blank=True)
    comment = models.TextField("комментарий", blank=True)
    object_type = models.ForeignKey(ObjectType, null=True, blank=True, on_delete=models.PROTECT,
                                    verbose_name="тип объекта")
    fully_depreciated = models.BooleanField("полностью амортизирован", null=True, blank=True)
    subdivision = models.TextField("подразделение", blank=True)
    org_code = models.TextField("код организации (42→31)", blank=True)
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
    # Строка поиска: наименование, инв. номер, модель — нижний регистр, «ё» = «е».
    # Поиск не зависит от локали базы (в локали C PostgreSQL не меняет регистр кириллицы).
    search = models.TextField("строка поиска", blank=True, editable=False)
    row_hash = models.CharField("хеш строки", max_length=32)
    extra = models.JSONField("столбцы вне схемы", default=dict, blank=True)

    class Meta:
        verbose_name = "позиция"
        verbose_name_plural = "позиции"
        indexes = [models.Index(fields=["inventory", "sheet", "row_no"], name="item_inv_sheet_row"),
                   GinIndex(fields=["search"], opclasses=["gin_trgm_ops"], name="item_search_trgm")]

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
    account = models.TextField("счёт")
    src_initial = models.DecimalField("итог источника: первонач.", **MONEY)
    sum_initial = models.DecimalField("сумма позиций: первонач.", **MONEY)
    src_dep = models.DecimalField("итог источника: износ", **MONEY)
    sum_dep = models.DecimalField("сумма позиций: износ", **MONEY)
    matches = models.TextField("совпадает", blank=True)

    class Meta:
        verbose_name = "сверка с источником"
        verbose_name_plural = "сверки с источником"


class Rule(models.Model):
    """«Правила» описи: как заполнен каждый столбец — хранятся при каждой версии."""
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="rules")
    order = models.PositiveIntegerField("порядок")
    field = models.TextField("поле")
    text = models.TextField("как заполнено")

    class Meta:
        verbose_name = "правило описи"
        verbose_name_plural = "правила описи"
        ordering = ["inventory", "order"]
