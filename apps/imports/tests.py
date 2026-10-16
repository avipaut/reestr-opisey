"""Импорт описей: разбор, обход с хешами, приём, история «на дату».

Книги строятся здесь же — описи пользователя в репозиторий не кладутся.
Каждый сценарий ловит конкретную ошибку (в имени теста — какую).
"""

import datetime as dt
import os
import shutil
import tempfile
import time

import openpyxl
from openpyxl.styles import PatternFill
from django.test import TransactionTestCase
from django.utils import timezone

from apps.inventories.models import Inventory, Item, Status
from apps.orgs.models import FolderMapping, Node
from apps.stats import services as stats

from . import services
from .models import ScanRun
from .reader import parse_book

HEAD = ["Основное средство", "Код учёта", "Количество", "Инв.Номер", "Первонач. Стоимость", "Накоп. Износ",
        "Балансовая стоимость", "Приоритет", "Материальное ответственное лицо", "Рекомендуемый код", "Ведомство",
        "Подведомство", "Модель средства", "Рабочее или нет", "Статус", "Причина", "Комментарий",
        "Тип объекта (определён)", "Полностью амортизирован", "Подразделение", "Код организации (42→31)",
        "Строка источника", "Сальдо нач.: первонач.", "Сальдо нач.: износ", "Сальдо нач.: балансовая",
        "Приход: первонач.", "Приход: износ", "Расход: первонач.", "Расход: износ", "Доп. сведения источника",
        "Отметка пользователя (02.10.2026)"]


def row(name, code, status, qty=1, inv="1", cost=10000, mark=""):
    return [name, 31122110, qty, inv, cost, 0, cost, 4, None, code, None, "Тестовое управление", None, None,
            status, "причина", "комментарий", "Офисное оборудование", "Нет", None, 31122110, 5, 0, 0, 0, 0, 0, 0, 0,
            "Папка источника: 126\\8. Тест; Файл / лист источника: ОСВ 18.11. 2025.xls / ПЕРЕЧЕНЬ", mark]


def make_book(path, rows, removed=(), fill_first=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Опись"
    ws.append(HEAD)
    for r in rows:
        ws.append(r)
    if fill_first:
        ws["A2"].fill = PatternFill("solid", fgColor=fill_first)
    ws2 = wb.create_sheet("Вынесено")
    ws2.append(HEAD)
    for r in removed:
        ws2.append(r)
    sv = wb.create_sheet("Сводка")
    sv.append(["Тестовое управление"])
    sv.append(["Источник: !ПРОВЕРИТЬ — ОСВ 18.11. 2025 (1).xlsx — 1С ОСВ по ОС; лист «ПЕРЕЧЕНЬ»; иерархия: Счет / ОС"])
    sv.append(["Стоимости — сальдо на конец периода"])
    sv.append([])
    sv.append(["Сверка с итогами источника", "Итог источника: первонач.", "Сумма позиций: первонач.",
               "Итог источника: износ", "Сумма позиций: износ", "Совпадает"])
    sv.append(["Счёт 31122110", 100, 120, 0, 0, "НЕТ — расхождение в источнике"])
    pr = wb.create_sheet("Правила")
    pr.append(["Поле", "Как заполнено"])
    pr.append(["Код учёта", "Из источника"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)


BASE = [row("Компьютер", "31123130", "Основной список", inv="10"),
        row("Принтер", "31123120", "Резерв", qty=None, inv="М-205"),
        row("Стул", "31123110", "Не требует оценки", qty=3, inv="12", mark="красный")]


class ReaderTests(TransactionTestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_all_fields_summary_rules_marks(self):
        """Все столбцы, «Сводка», «Правила», заливка и отметка; пустое количество — 1 с пометкой."""
        p = os.path.join(self.dir, "a.xlsx")
        make_book(p, BASE, removed=[row("Здание", "31112120", "Вынесено", inv="1")], fill_first="FFFF00")
        r = parse_book(p)
        self.assertEqual(r["error"], "")
        self.assertEqual(len(r["items"]), 4)
        first, printer, chair = r["items"][:3]
        self.assertEqual(first["name_fill"], "FFFF00")
        self.assertEqual(first["recommended_code"], "31123130")
        self.assertEqual(first["account_code"], "31122110")
        self.assertEqual(printer["qty"], "1")
        self.assertEqual(printer["qty_note"], "пусто — за 1")
        self.assertEqual(printer["inv_key"], "м205")
        self.assertEqual(chair["user_mark"], "красный")
        self.assertEqual(chair["user_mark_date"], "2026-10-02")
        self.assertIsNone(first["user_mark_date"], "дата отметки — только у строк с отметкой")
        self.assertEqual(r["source_name"], "ОСВ 18.11. 2025.xls", "имя исходного файла — из «Доп. сведений»")
        self.assertEqual(r["source_kind"], "1С ОСВ по ОС", "вид источника — после последнего « — »")
        self.assertEqual(r["source_date"], "2025-11-18")
        self.assertEqual(r["cost_basis"], "сальдо на конец периода")
        self.assertEqual(len(r["reconciliations"]), 1)
        self.assertEqual(r["rules"][0]["field"], "Код учёта")

    def test_broken_book_is_an_error_not_a_crash(self):
        p = os.path.join(self.dir, "b.xlsx")
        open(p, "wb").write(b"not a zip")
        r = parse_book(p)
        self.assertIn("не открывается", r["error"])


class ScanTests(TransactionTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.folder = os.path.join(self.root, "126", "8. Тестовое ДНВТ")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def scan(self, **kw):
        return services.scan([self.root], workers=kw.get("workers", 1))

    def test_scan_skips_unchanged_and_copies(self):
        """Повторный обход не открывает книги; копия того же файла не разбирается."""
        make_book(os.path.join(self.folder, "опись.xlsx"), BASE)
        run = self.scan()
        self.assertEqual((run.found, run.parsed, run.pending, run.errors), (1, 1, 1, 0))
        run = self.scan()
        self.assertEqual((run.unchanged, run.parsed), (1, 0))
        shutil.copy(os.path.join(self.folder, "опись.xlsx"), os.path.join(self.root, "копия.xlsx"))
        run = self.scan()
        self.assertEqual((run.moved, run.parsed), (1, 0))

    def test_accept_creates_nodes_and_stats(self):
        make_book(os.path.join(self.folder, "опись.xlsx"), BASE)
        self.scan()
        inv = Inventory.objects.get(state=Inventory.State.PENDING)
        self.assertIsNone(inv.node, "узла ещё нет — создаётся при приёме")
        services.accept(inv)
        node = Node.objects.get(path="/126/8/")
        self.assertEqual((node.code, node.name, node.parent.code), ("8", "Тестовое ДНВТ", "126"))
        rows = {(r["code"], r["status__name"]): r["qty"] for r in stats.by_code(timezone.now())}
        self.assertEqual(rows[("31123110", "Не требует оценки")], 3)
        self.assertEqual(sum(rows.values()), 5)

    def test_history_on_date_uses_version_period(self):
        """Свод на дату — по версии, действовавшей тогда (граф APCS: debt-position-at-time-broken)."""
        p = os.path.join(self.folder, "опись.xlsx")
        make_book(p, BASE)
        self.scan()
        services.accept(Inventory.objects.get(state=Inventory.State.PENDING))
        between = timezone.now()
        time.sleep(1.1)
        make_book(p, BASE + [row("Сервер", "31123130", "Основной список", inv="99")])
        self.scan()
        new = Inventory.objects.get(state=Inventory.State.PENDING)
        services.accept(new)
        old = Inventory.objects.exclude(pk=new.pk).get(state=Inventory.State.ACCEPTED)
        self.assertFalse(old.is_current)
        self.assertIsNotNone(old.valid_to)
        total = lambda at: sum(r["qty"] for r in stats.by_code(at))
        self.assertEqual(total(between), 5)
        self.assertEqual(total(timezone.now()), 6)

    def test_resaved_book_with_same_rows_is_unchanged(self):
        """Пересохранённая книга (другой SHA-256, те же строки) новой версией не становится."""
        p = os.path.join(self.folder, "опись.xlsx")
        make_book(p, BASE)
        self.scan()
        services.accept(Inventory.objects.get(state=Inventory.State.PENDING))
        time.sleep(1.1)
        make_book(p, BASE)  # тот же состав, новая запись файла
        run = self.scan()
        self.assertEqual(run.same_content + run.moved, 1)
        self.assertFalse(Inventory.objects.filter(state=Inventory.State.PENDING).exists())

    def test_folder_without_code_needs_node_and_is_remembered(self):
        make_book(os.path.join(self.root, "Архив", "опись.xlsx"), BASE)
        self.scan()
        inv = Inventory.objects.get(state=Inventory.State.PENDING)
        self.assertFalse(inv.node_resolved)
        with self.assertRaises(ValueError):
            services.accept(inv)
        node = Node(code="200", name="Выбрано вручную")
        node.save()
        services.accept(inv, node=node)
        self.assertTrue(FolderMapping.objects.filter(folder="Архив", node=node).exists())

    def test_rejected_is_not_offered_again(self):
        make_book(os.path.join(self.folder, "опись.xlsx"), BASE)
        self.scan()
        inv = services.reject(Inventory.objects.get(state=Inventory.State.PENDING))
        self.assertEqual(Item.objects.filter(inventory=inv).count(), 0)
        os.utime(os.path.join(self.folder, "опись.xlsx"))  # тронули файл — содержимое то же
        run = self.scan()
        self.assertEqual(run.parsed, 0)

    def test_broken_file_counts_as_error(self):
        """Отказ не выглядит успехом (граф APCS: debt-import-swallows-failures)."""
        os.makedirs(self.folder)
        open(os.path.join(self.folder, "битая.xlsx"), "wb").write(b"x")
        run = self.scan()
        self.assertEqual(run.state, ScanRun.State.DONE_ERRORS)
        self.assertEqual(run.errors, 1)
        self.assertIn("битая.xlsx", run.log)

    def test_parallel_parse_in_processes(self):
        """Разбор в процессах (spawn на Windows) даёт те же описи."""
        for i in range(3):
            make_book(os.path.join(self.root, "126", f"{i + 1}. Подвед {i}", "опись.xlsx"),
                      BASE + [row(f"Вещь {i}", "31123130", "Резерв", inv=f"x{i}")])
        run = self.scan(workers=2)
        self.assertEqual((run.parsed, run.pending, run.errors), (3, 3, 0))
        self.assertEqual(Item.objects.count(), 12)
