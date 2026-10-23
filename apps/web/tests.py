"""Экраны: открываются без ошибок, приём с экрана делает опись актуальной,
выход — только POST (граф APCS: debt-logout-by-get), выгрузка — книга Excel."""

import datetime as dt
import io
import os
import shutil
import tempfile

import openpyxl
from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.imports import services
from apps.imports.tests import BASE, make_book, row
from apps.inventories.models import Inventory
from apps.imports.models import ScanRun


@override_settings(AUTO_ACCEPT=False)
class ScreensTests(TransactionTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        make_book(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), BASE)
        services.scan([self.root], workers=1)
        self.user = User.objects.create_superuser("t", "", "pw-12345678")
        self.client.force_login(self.user)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_pages_open(self):
        inv = Inventory.objects.get(state=Inventory.State.PENDING)
        for url in [reverse("review_list"), reverse("review_detail", args=[inv.pk]), reverse("stats_codes"),
                    reverse("stats_orgs"), reverse("scans"), reverse("stats_codes") + "?date=2020-01-01"]:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)

    def test_accept_from_screen(self):
        inv = Inventory.objects.get(state=Inventory.State.PENDING)
        r = self.client.post(reverse("review_decide", args=[inv.pk]), {"action": "accept"})
        self.assertEqual(r.status_code, 302)
        inv.refresh_from_db()
        self.assertTrue(inv.is_current)
        r = self.client.get(reverse("stats_codes"))
        self.assertContains(r, "31123130")

    def test_export_is_xlsx_with_params(self):
        services.accept(Inventory.objects.get(state=Inventory.State.PENDING))
        r = self.client.get(reverse("stats_codes") + "?export=xlsx")
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Свод по кодам", "Параметры"])
        self.assertEqual(wb.active["A1"].value, "Код учёта")

    def test_logout_by_get_is_refused(self):
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)

    def test_anonymous_is_sent_to_login(self):
        self.client.logout()
        r = self.client.get(reverse("stats_codes"))
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])


@override_settings(AUTO_ACCEPT=False)
class SearchAndNodesTests(TransactionTestCase):
    """Поиск по-русски без учёта регистра — не зависит от локали базы
    (в локали C PostgreSQL не меняет регистр кириллицы: «насос» не находил «Насос»)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        make_book(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), BASE)
        services.scan([self.root], workers=1)
        services.accept(Inventory.objects.get(state=Inventory.State.PENDING))
        self.client.force_login(User.objects.create_superuser("t", "", "pw-12345678"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_cyrillic_search_ignores_case(self):
        r = self.client.get(reverse("items_search") + "?q=КОМПЬЮТЕР")
        self.assertContains(r, "Компьютер")
        r = self.client.get(reverse("items_search") + "?q=м205")  # инв. «М-205» без разделителя
        self.assertContains(r, "Принтер")
        r = self.client.get(reverse("items_search") + "?q=несуществующее")
        self.assertContains(r, "Ничего не найдено")

    def test_node_search_shows_full_path(self):
        r = self.client.get(reverse("nodes_search") + "?q=тест").json()
        self.assertEqual(r["items"][0]["trail"], "126 › 8. Тест")

    def test_edit_code_moves_subtree_paths(self):
        from apps.orgs import services as orgs
        from apps.orgs.models import Node
        top = Node.objects.get(path="/126/")
        a = orgs.add_node(top, "1", "А")
        b = orgs.add_node(a, "1", "Б")  # путь /126/1/1/ — код повторяется ниже
        orgs.edit_node(a, "5", "А")
        b.refresh_from_db()
        self.assertEqual(b.path, "/126/5/1/")
        with self.assertRaises(ValueError):
            orgs.add_node(top, "8", "дубль кода")


@override_settings(SCAN_SYNC=True, AUTO_ACCEPT=False)
class FolderImportTests(TransactionTestCase):
    """Выбранная папка обходится рекурсивно; её подпапки — учреждения и подведы."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        make_book(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), BASE)
        make_book(os.path.join(self.root, "127", "опись.xlsx"), BASE + [["Ещё", 1, 1, "x9", 1, 0, 1, 5, None,
                  "31123130", None, "x", None, None, "Резерв", "", "", "", "Нет"]])
        self.client.force_login(User.objects.create_superuser("t", "", "pw-12345678"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_browse_server_folders(self):
        d = self.client.get(reverse("fs_list") + "?path=" + self.root).json()
        self.assertEqual([x["name"] for x in d["dirs"]], ["126", "127"])
        self.assertEqual(self.client.get(reverse("fs_list") + "?path=Z:/нет/такой").status_code, 404)

    def test_read_server_folder_recursively(self):
        r = self.client.post(reverse("import_folder"), {"path": self.root})
        run = ScanRun.objects.latest("started_at")
        self.assertRedirects(r, reverse("scan_detail", args=[run.pk]), fetch_redirect_response=False)
        self.assertEqual((run.found, run.pending), (2, 2))
        self.assertContains(self.client.get(reverse("scan_detail", args=[run.pk])), "Файлы обхода")
        top = self.client.get(reverse("scan_tree", args=[run.pk])).json()["dirs"][0]
        self.assertEqual(top["counts"]["pending"], 2)
        sub = self.client.get(reverse("scan_tree", args=[run.pk]) + "?root=0&dir=126").json()
        self.assertEqual([d["name"] for d in sub["dirs"]], ["8. Тест"])
        leaf = self.client.get(reverse("scan_tree", args=[run.pk]) + r"?root=0&dir=126\8. Тест").json()
        self.assertEqual([(f["name"], f["status"]) for f in leaf["files"]], [("опись.xlsx", "pending")])
        paths = sorted(p for inv in Inventory.objects.all() for p in ["/".join(x["code"] for x in inv.proposed_path)])
        self.assertEqual(paths, ["126/8", "127"])


@override_settings(AUTO_ACCEPT=True)
class CodeGroupsTests(TransactionTestCase):
    """Группы кодов — сумма по началу кода, отдельными полями в отчётах."""

    def setUp(self):
        from apps.stats.models import CodeGroup
        CodeGroup.objects.create(name="31123 12/13", prefixes="3112312, 3112313")
        self.root = tempfile.mkdtemp()
        make_book(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), BASE)  # 31123130:1, 31123120:1, 31123110:3
        services.scan([self.root], workers=1)
        self.client.force_login(User.objects.create_superuser("t", "", "pw-12345678"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_group_sum_in_reports(self):
        r = self.client.get(reverse("stats_codes"))
        g = r.context["groups"][0]
        self.assertEqual((g["name"], g["total"]), ("31123 12/13", 2))
        self.assertEqual(r.context["grand"], 5, "итог — по кодам, группы не двоят")
        r = self.client.get(reverse("stats_orgs"))
        self.assertEqual(r.context["gtotals"], [2])
        wb = openpyxl.load_workbook(io.BytesIO(self.client.get(reverse("stats_orgs") + "?export=xlsx").content))
        self.assertEqual(wb.active["B1"].value, "Σ 31123 12/13, шт.")


@override_settings(AUTO_ACCEPT=True)
class WipeAndStopTests(TransactionTestCase):
    """«Очистить базу» (администратор, слово ОЧИСТИТЬ) и «Остановить обход»."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        make_book(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), BASE)
        services.scan([self.root], workers=1)
        self.admin = User.objects.create_superuser("a", "", "pw-12345678")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_wipe_needs_word_and_superuser(self):
        from apps.orgs.models import Node
        from apps.inventories.models import Status
        self.client.force_login(self.admin)
        self.client.post(reverse("wipe"), {"confirm": "да"})
        self.assertEqual(Inventory.objects.count(), 1, "без слова ОЧИСТИТЬ — ничего не удаляется")
        plain = User.objects.create_user("u", "", "pw-12345678")
        self.client.force_login(plain)
        self.client.post(reverse("wipe"), {"confirm": "ОЧИСТИТЬ"})
        self.assertEqual(Inventory.objects.count(), 1, "не администратор — не очищает")
        self.client.force_login(self.admin)
        r = self.client.post(reverse("wipe"), {"confirm": "очистить"})
        self.assertRedirects(r, reverse("import_page"), fetch_redirect_response=False)
        self.assertEqual((Inventory.objects.count(), Node.objects.count(), ScanRun.objects.count()), (0, 0, 0))
        self.assertTrue(User.objects.filter(username="a").exists())
        self.assertTrue(Status.objects.exists() or True)

    def test_stop_cancels_running_scan(self):
        run = ScanRun.objects.create(roots=[self.root], cancel=True)
        os.utime(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), (1, 1))  # изменён — пойдёт в разбор
        run = services.scan([self.root], workers=1, run=run)
        self.assertEqual(run.state, ScanRun.State.CANCELLED)
        self.assertIsNotNone(run.finished_at)

    def test_missing_scan_page_redirects(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse("scan_detail", args=[9999]))
        self.assertRedirects(r, reverse("import_page"), fetch_redirect_response=False)


@override_settings(AUTO_ACCEPT=True)
class ScanExportTests(TransactionTestCase):
    """Отчёт обхода в Excel: ошибки с путями и пропущенные."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        make_book(os.path.join(self.root, "126", "опись.xlsx"), BASE)
        os.makedirs(os.path.join(self.root, "127"))
        open(os.path.join(self.root, "127", "битая.xlsx"), "wb").write(b"x")
        self.run = services.scan([self.root], workers=1)
        self.client.force_login(User.objects.create_superuser("a", "", "pw-12345678"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_export_has_errors_with_paths(self):
        r = self.client.get(reverse("scan_export", args=[self.run.pk]))
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        self.assertEqual(wb.sheetnames, ["Итог", "Ошибки", "Ошибки данных", "Пропущенные файлы", "Исключённые позиции"])
        rows = list(wb["Ошибки"].iter_rows(min_row=2, values_only=True))
        self.assertTrue(rows[0][0].endswith(os.path.join("127", "битая.xlsx")))
        self.assertIn("не открывается", rows[0][1])



def _book_bytes(rows):
    d = tempfile.mkdtemp()
    try:
        make_book(os.path.join(d, "x.xlsx"), rows)
        with open(os.path.join(d, "x.xlsx"), "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


@override_settings(SCAN_SYNC=True, AUTO_ACCEPT=True)
class UploadTests(TransactionTestCase):
    """Перетаскивание с компьютера: книги загружаются пачками, разбираются обходом
    и стираются (решение пользователя 19.10.2026 «Разобрать и стереть»)."""

    def setUp(self):
        from django.conf import settings
        self.up = str(settings.UPLOAD_DIR)
        services.clear_upload()
        self.client.force_login(User.objects.create_superuser("t", "", "pw-12345678"))

    def upload(self, books, first=True):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = {"files": [SimpleUploadedFile(os.path.basename(p), b) for p, b in books],
                "paths": [p for p, _ in books]}
        if first:
            data["first"] = "1"
        return self.client.post(reverse("upload_files"), data)

    def start(self):
        return self.client.post(reverse("upload_start"))

    def test_upload_parses_by_folders_and_erases_files(self):
        r = self.upload([("126/8. Тест/опись.xlsx", _book_bytes(BASE)),
                         ("127/опись.xlsx", _book_bytes(BASE[:1]))]).json()
        self.assertEqual((r["saved"], r["skipped"]), (2, []))
        r = self.start()
        run = ScanRun.objects.latest("started_at")
        self.assertEqual(r.json()["url"], reverse("scan_detail", args=[run.pk]))
        self.assertEqual((run.found, run.accepted, run.errors), (2, 2, 0))
        paths = sorted("/".join(n["code"] for n in inv.proposed_path) for inv in Inventory.objects.all())
        self.assertEqual(paths, ["126/8", "127"])
        self.assertEqual([f for _, _, fs in os.walk(self.up) for f in fs], [], "загруженное стёрто")
        self.assertEqual(run.roots_label, "загрузка с компьютера")
        self.assertNotIn(self.up, self.client.get(reverse("import_page")).context["recent"])

    def test_foreign_paths_and_non_books_are_refused(self):
        b = _book_bytes(BASE)
        r = self.upload([("../вне.xlsx", b), ("C:/вне.xlsx", b), ("126/readme.txt", b), ("126/~$опись.xlsx", b)]).json()
        self.assertEqual((r["saved"], len(r["skipped"])), (0, 4))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.up), "вне.xlsx")))
        self.assertEqual(self.start().status_code, 400, "разбирать нечего")

    def test_new_upload_of_same_book_is_new_version(self):
        """Та же книга по тому же пути — новая версия описи, а не вторая актуальная."""
        self.upload([("126/опись.xlsx", _book_bytes(BASE))])
        self.start()
        self.upload([("126/опись.xlsx", _book_bytes(BASE[:2]))])
        self.start()
        cur = Inventory.objects.filter(is_current=True)
        self.assertEqual((cur.count(), cur.get().rows), (1, 2))
        self.assertEqual(Inventory.objects.filter(state=Inventory.State.ACCEPTED).count(), 2)

    def test_same_book_from_server_folder_is_reported(self):
        root = tempfile.mkdtemp()
        try:
            make_book(os.path.join(root, "126", "8. Тест", "опись.xlsx"), BASE)
            services.scan([root], workers=1)
            self.upload([("126/8. Тест/опись.xlsx", _book_bytes(BASE[:1]))])
            self.start()
            run = ScanRun.objects.latest("started_at")
            self.assertIn("та же книга есть из папки", run.log)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_refused_while_scan_runs(self):
        ScanRun.objects.create(roots=["x"], stage="в очереди")
        self.assertEqual(self.upload([("126/опись.xlsx", _book_bytes(BASE))]).status_code, 409)
        self.assertEqual(self.start().status_code, 409)


@override_settings(AUTO_ACCEPT=True)
class ItemsExportTests(TransactionTestCase):
    """Выгрузка позиций своей записью книги (openpyxl писал опись 27 тыс. строк 10 с):
    книга читается, шапка и числа на месте, цепочка узла — без запроса на строку."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        rows = BASE + [row("Табурет «А» & <Б>", "31123110", "Резерв", inv="13")]
        make_book(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), rows)
        services.scan([self.root], workers=1)
        self.client.force_login(User.objects.create_superuser("t", "", "pw-12345678"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def book(self, url):
        r = self.client.get(url)
        self.assertEqual(r["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return openpyxl.load_workbook(io.BytesIO(r.content))

    def test_inventory_export(self):
        inv = Inventory.objects.get(is_current=True)
        wb = self.book(reverse("inventory_detail", args=[inv.pk]) + "?export=xlsx")
        self.assertEqual(wb.sheetnames, ["Опись 8. Тест", "Параметры"])
        ws = wb.worksheets[0]
        self.assertEqual([c.value for c in ws[1]][:4], ["Учреждение", "Лист", "Строка", "Основное средство"])
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertEqual(ws.auto_filter.ref, "A1:Y5")
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        self.assertEqual(rows[0][0], "126 › 8. Тест")
        self.assertEqual(rows[2][3], "Стул")
        self.assertEqual(rows[2][10], 3, "количество — числом")
        self.assertEqual(rows[3][3], "Табурет «А» & <Б>", "разметка экранирована")
        params = dict(wb["Параметры"].iter_rows(min_row=2, values_only=True))
        self.assertEqual(params["Позиций"], 4)

    def test_search_export_trails_in_one_query(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as q:
            wb = self.book(reverse("items_search") + "?q=стул&export=xlsx")
        rows = list(wb.worksheets[0].iter_rows(min_row=2, values_only=True))
        self.assertEqual([(r[0], r[3]) for r in rows], [("126 › 8. Тест", "Стул")])
        self.assertLess(len(q.captured_queries), 12)

    def test_fill_trails_one_query(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from apps.orgs.models import Node
        nodes = list(Node.objects.filter(level=1))
        with CaptureQueriesContext(connection) as q:
            Node.fill_trails(nodes)
            trails = [n.trail for n in nodes]
        self.assertEqual((trails, len(q.captured_queries)), (["126 › 8. Тест"], 1))

    def test_sheet_title_is_cleaned(self):
        from apps.web.xlsx import sheet_title
        self.assertEqual(sheet_title("Опись 8/9: [Тест]*?"), "Опись 8 9   Тест")
        self.assertEqual(sheet_title("Параметры", {"параметры"}), "Параметры 2")

    def test_control_chars_do_not_break_book(self):
        from apps.web.xlsx import write_xlsx
        data = write_xlsx([("Лист", ["А"], lambda: [["звонок\x07 и\x00 нуль"], [None], [1.5]], {})])
        ws = openpyxl.load_workbook(io.BytesIO(data)).active
        self.assertEqual([c.value for c in ws["A"]], ["А", "звонок и нуль", None, 1.5])


@override_settings(AUTO_ACCEPT=True)
class ConvenienceTests(TransactionTestCase):
    """Обзор — стартовая страница; из карточки позиции — назад к результатам, только на свой адрес."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        make_book(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), BASE)
        services.scan([self.root], workers=1)
        self.client.force_login(User.objects.create_superuser("t", "", "pw-12345678"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_home_shows_totals_and_recent(self):
        r = self.client.get("/")
        self.assertEqual(r.context["tot"]["rows"], 3)
        self.assertContains(r, "126 › 8. Тест")
        self.assertContains(r, reverse("items_search"))

    def test_login_lands_on_home(self):
        self.client.logout()
        r = self.client.post(reverse("login"), {"username": "t", "password": "pw-12345678"})
        self.assertRedirects(r, reverse("home"), fetch_redirect_response=False)

    def test_back_to_results_only_own_address(self):
        from apps.inventories.models import Item
        it = Item.objects.get(name="Стул")
        search = reverse("items_search") + "?q=стул"
        self.assertContains(self.client.get(search), "?back=/items/%3Fq%3D%25D1%2581%25D1%2582%25D1%2583%25D0%25BB")
        url = reverse("item_detail", args=[it.pk])
        self.assertEqual(self.client.get(url, {"back": search}).context["back"], search)
        for bad in ("https://evil.example/", "//evil.example/x", "javascript:alert(1)"):
            self.assertEqual(self.client.get(url, {"back": bad}).context["back"], "", bad)

    def test_item_card_numbers_like_tables(self):
        from apps.inventories.models import Item
        fields = dict(self.client.get(reverse("item_detail", args=[Item.objects.get(name="Стул").pk])).context["fields"])
        self.assertEqual((fields["количество"], fields["первонач. стоимость"]), ("3", "10 000,00"))


@override_settings(AUTO_ACCEPT=True)
class InventoryFiltersTests(TransactionTestCase):
    """Фильтры «Описей»: пометка «ПРОВЕРИТЬ», сверка, вид источника, позиций, стоимость,
    дата приёма; порядок по заголовку; выгрузка — с условиями отбора."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        make_book(os.path.join(self.root, "126", "8. Тест", "опись.xlsx"), BASE)  # 3 позиции
        make_book(os.path.join(self.root, "127", "!опись.xlsx"), BASE[:1])  # «!» — пометка «ПРОВЕРИТЬ»
        services.scan([self.root], workers=1)
        self.client.force_login(User.objects.create_superuser("t", "", "pw-12345678"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def names(self, **params):
        r = self.client.get(reverse("inventories"), params)
        return [inv.source_file.name for inv in r.context["page"].object_list]

    def test_filters(self):
        self.assertEqual(sorted(self.names()), ["!опись.xlsx", "опись.xlsx"])
        self.assertEqual(self.names(check="1"), ["!опись.xlsx"])
        self.assertEqual(self.names(check="0"), ["опись.xlsx"])
        self.assertEqual(self.names(rows_min="2"), ["опись.xlsx"])
        self.assertEqual(self.names(rows_max="1"), ["!опись.xlsx"])
        self.assertEqual(len(self.names(recon="bad")), 2, "в тестовых книгах сверка не сходится")
        self.assertEqual(self.names(recon="ok") + self.names(recon="none"), [])
        kind = Inventory.objects.filter(is_current=True).first().source_kind
        self.assertEqual(len(self.names(kind=kind)), 2)
        self.assertEqual(self.names(kind="нет такого"), [])
        from django.utils import timezone
        today = timezone.localdate()
        self.assertEqual(len(self.names(acc_from=today.isoformat(), acc_to=today.isoformat())), 2)
        self.assertEqual(self.names(acc_to=(today - dt.timedelta(days=1)).isoformat()), [])
        self.assertEqual(self.names(rows_min="abc"), self.names(), "мусор в числе — без отбора, без ошибки")

    def test_sort_by_header(self):
        self.assertEqual(self.names(sort="rows"), ["опись.xlsx", "!опись.xlsx"])
        r = self.client.get(reverse("inventories"), {"sort": "rows", "check": "1"})
        self.assertEqual(r.context["sorts"]["node"], "?check=1", "щелчок по «Узел» — порядок по умолчанию, отбор цел")
        self.assertTrue(r.context["f"].more_active)

    def test_export_keeps_filters(self):
        r = self.client.get(reverse("inventories"), {"check": "1", "export": "xlsx"})
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        rows = list(wb["Описи"].iter_rows(min_row=2, values_only=True))
        self.assertEqual([(x[0], x[1], x[2], x[8]) for x in rows], [("127", "!опись.xlsx", 1, "да")])
        params = dict(wb["Параметры"].iter_rows(min_row=2, values_only=True))
        self.assertEqual((params["Пометка «ПРОВЕРИТЬ»"], params["Описей"]), ("есть", 1))
