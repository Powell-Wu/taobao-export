# -*- coding: utf-8 -*-
import copy
import csv
import json
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import load_workbook

import app


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        app.OUT_DIR = root / "out"
        app.DATA_DIR = root / "data"
        app.OUT_DIR.mkdir()
        app.DATA_DIR.mkdir()
        app.DATA_FILES = {
            "orders": app.DATA_DIR / "orders.json",
            "cart": app.DATA_DIR / "cart.json",
            "demo": app.DATA_DIR / "demo.json",
        }
        app.LEGACY_LATEST = app.DATA_DIR / "latest.json"
        app.STORE = {
            "orders": copy.deepcopy(app.DEMO),
            "scrapedAt": "2026-01-01 00:00:00",
            "source": "orders",
        }
        app.JOB = {"running": False, "logs": [], "done": False, "error": None}
        self.client = app.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_state_and_static_resources(self):
        state = self.client.get("/api/state").get_json()
        self.assertEqual(state["version"], "1.0.0")
        self.assertEqual(state["itemCount"], 3)
        self.assertEqual(state["source"], "orders")
        for path in ("/", "/static/app.js", "/static/style.css"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_default_excel_export(self):
        response = self.client.post("/api/export", json={
            "ids": ["0-0", "1-1"],
            "fields": [{"key": k, "label": app.FIELD_MAP[k]["label"]} for k in app.DEFAULT_KEYS],
            "format": "xlsx",
        })
        result = response.get_json()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["total"], 2644.65)

        workbook = load_workbook(app.OUT_DIR / result["name"])
        sheet = workbook.active
        self.assertEqual([c.value for c in sheet[1]],
                         ["序号", "商品名称", "规格/型号", "数量", "页面单价", "小计", "店铺", "商品链接"])
        self.assertEqual(sheet["F2"].value, 2598)
        self.assertEqual(sheet["F3"].value, 46.65)
        self.assertEqual(sheet["F5"].value, 2644.65)
        self.assertEqual(sheet.freeze_panes, "A2")
        self.assertIsNotNone(sheet["H2"].hyperlink)

    def test_custom_csv_and_formula_injection_protection(self):
        app.STORE["orders"][0]["items"][0]["title"] = "=HYPERLINK(\"bad\")"
        response = self.client.post("/api/export", json={
            "ids": ["0-0"],
            "fields": [{"key": "title", "label": "=自定义列名"}, {"key": "subtotal", "label": "金额"}],
            "format": "csv",
        })
        result = response.get_json()
        with open(app.OUT_DIR / result["name"], encoding="utf-8-sig", newline="") as fp:
            rows = list(csv.reader(fp))
        self.assertEqual(rows[0][0], "'=自定义列名")
        self.assertEqual(rows[1][0], "'=HYPERLINK(\"bad\")")
        self.assertEqual(rows[1][1], "2598.0")

    def test_export_names_do_not_collide(self):
        payload = {"ids": ["0-0"], "format": "csv"}
        first = self.client.post("/api/export", json=payload).get_json()
        second = self.client.post("/api/export", json=payload).get_json()
        self.assertNotEqual(first["name"], second["name"])

    def test_item_edit_is_reflected_in_export(self):
        response = self.client.post("/api/items", json={"patch": {"1-1": {"qty": 5, "spec": "改后规格"}}})
        self.assertTrue(response.get_json()["ok"])
        result = self.client.post("/api/export", json={
            "ids": ["1-1"], "fields": [{"key": "spec"}, {"key": "subtotal"}], "format": "csv"
        }).get_json()
        with open(app.OUT_DIR / result["name"], encoding="utf-8-sig", newline="") as fp:
            rows = list(csv.reader(fp))
        self.assertEqual(rows[1], ["改后规格", "77.75"])

    def test_demo_does_not_overwrite_orders(self):
        app.save_store()
        original = app.DATA_FILES["orders"].read_bytes()
        response = self.client.post("/api/demo")
        self.assertTrue(response.get_json()["ok"])
        self.assertEqual(app.STORE["source"], "demo")
        self.assertEqual(app.DATA_FILES["orders"].read_bytes(), original)
        self.assertTrue(app.DATA_FILES["demo"].exists())

    def test_load_store_prefers_latest_real_dataset(self):
        app.save_store()
        app.STORE["source"] = "cart"
        app.STORE["orders"] = []
        app.STORE["scrapedAt"] = "2027-01-01 00:00:00"
        app.save_store()
        app.STORE = {"orders": [], "scrapedAt": None, "source": "orders"}
        app.load_store()
        self.assertEqual(app.STORE["source"], "cart")

    def test_invalid_requests_are_rejected(self):
        self.assertEqual(self.client.post("/api/scrape", json={"source": "bad"}).status_code, 400)
        self.assertEqual(self.client.post("/api/export", json={"ids": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/export", json={
            "ids": ["0-0"], "fields": [{"key": "unknown"}], "format": "csv"
        }).status_code, 400)
        self.assertEqual(self.client.post("/api/export", json={
            "ids": ["0-0"], "format": "exe"
        }).status_code, 400)
        self.assertEqual(self.client.post("/api/items", json={"patch": []}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
