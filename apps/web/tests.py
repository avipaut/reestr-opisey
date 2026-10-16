"""Экраны: открываются без ошибок, приём с экрана делает опись актуальной,
выход — только POST (граф APCS: debt-logout-by-get), выгрузка — книга Excel."""

import io
import os
import shutil
import tempfile

import openpyxl
from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.imports import services
from apps.imports.tests import BASE, make_book
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
        self.assertContains(self.client.get(reverse("scan_detail", args=[run.pk])), "Ждут просмотра")
        paths = sorted(p for inv in Inventory.objects.all() for p in ["/".join(x["code"] for x in inv.proposed_path)])
        self.assertEqual(paths, ["126/8", "127"])
