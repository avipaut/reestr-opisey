"""Экраны: открываются без ошибок, приём с экрана делает опись актуальной,
выход — только POST (граф APCS: debt-logout-by-get), выгрузка — книга Excel."""

import io
import os
import shutil
import tempfile

import openpyxl
from django.contrib.auth.models import User
from django.test import TransactionTestCase
from django.urls import reverse

from apps.imports import services
from apps.imports.tests import BASE, make_book
from apps.inventories.models import Inventory


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
