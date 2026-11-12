# -*- coding: utf-8 -*-
import copy
import csv
import json
import subprocess
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
        self.assertEqual(state["version"], "1.1.0")
        self.assertEqual(state["itemCount"], len(app.flatten(app.DEMO)))
        self.assertEqual(state["source"], "orders")
        for path in ("/", "/static/app.js", "/static/style.css"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_demo_button_uses_post(self):
        response = self.client.get("/static/app.js")
        script = response.get_data(as_text=True)
        response.close()
        self.assertIn("api('/api/demo', {})", script)

    def test_import_does_not_read_saved_datasets(self):
        code = (
            "from pathlib import Path\n"
            "from unittest.mock import patch\n"
            "with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected data read')):\n"
            "    import app\n"
            "assert app.STORE['orders'] == []\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_demo_contains_fictional_procurement_edge_cases(self):
        items = app.flatten(app.DEMO)
        self.assertEqual(len(app.DEMO), 7)
        self.assertEqual(len(items), 11)
        self.assertTrue(8 <= len(items) <= 12)
        self.assertEqual({it["id"] for it in items if not it["spec"]}, {"1-0", "2-1"})
        self.assertEqual({it["id"] for it in items if "SMA" in it["title"]}, {"0-0", "1-0"})
        self.assertEqual({it["id"] for it in items if "内六角" in it["title"]}, {"4-1", "5-0"})
        for order in app.DEMO:
            self.assertTrue(order["orderNo"].startswith("DEMO-"))
            self.assertTrue(any(word in order["shop"] for word in ("演示", "样例", "虚构", "模拟", "示例")))
            self.assertAlmostEqual(order["paid"], sum(app.item_subtotal(it) for it in order["items"]))
        for item in items:
            self.assertTrue(item["url"].startswith("https://example.invalid/demo/"))
        self.assertAlmostEqual(sum(app.item_subtotal(it) for it in items), 1719.4)

    def test_default_excel_export(self):
        response = self.client.post("/api/export", json={
            "ids": ["0-0", "2-1"],
            "fields": [{"key": k, "label": app.FIELD_MAP[k]["label"]} for k in app.DEFAULT_KEYS],
            "format": "xlsx",
        })
        result = response.get_json()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["total"], 794.0)

        workbook = load_workbook(app.OUT_DIR / result["name"])
        sheet = workbook.active
        self.assertEqual([c.value for c in sheet[1]],
                         ["序号", "商品名称", "规格/型号", "数量", "页面单价", "小计", "店铺", "商品链接"])
        self.assertEqual(sheet["F2"].value, 114)
        self.assertEqual(sheet["F3"].value, 680)
        self.assertEqual(sheet["F5"].value, 794)
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
        self.assertEqual(rows[1][1], "114.0")

    def test_export_names_do_not_collide(self):
        payload = {"ids": ["0-0"], "format": "csv"}
        first = self.client.post("/api/export", json=payload).get_json()
        second = self.client.post("/api/export", json=payload).get_json()
        self.assertNotEqual(first["name"], second["name"])

    def test_item_edit_is_reflected_in_export(self):
        response = self.client.post("/api/items", json={"patch": {"6-1": {"qty": 5, "spec": "改后规格"}}})
        self.assertTrue(response.get_json()["ok"])
        result = self.client.post("/api/export", json={
            "ids": ["6-1"], "fields": [{"key": "spec"}, {"key": "subtotal"}], "format": "csv"
        }).get_json()
        with open(app.OUT_DIR / result["name"], encoding="utf-8-sig", newline="") as fp:
            rows = list(csv.reader(fp))
        self.assertEqual(rows[1], ["改后规格", "49.5"])

    def test_demo_does_not_overwrite_orders_or_cart(self):
        app.save_store()
        app.STORE["source"] = "cart"
        app.STORE["scrapedAt"] = "2026-01-02 00:00:00"
        app.save_store()
        original = {source: app.DATA_FILES[source].read_bytes() for source in ("orders", "cart")}

        response = self.client.post("/api/demo")
        result = response.get_json()
        self.assertTrue(result["ok"])
        self.assertEqual(result["itemCount"], len(app.flatten(app.DEMO)))
        self.assertEqual(app.STORE["source"], "demo")
        for source, content in original.items():
            self.assertEqual(app.DATA_FILES[source].read_bytes(), content)
        saved_demo = json.loads(app.DATA_FILES["demo"].read_text(encoding="utf-8"))
        self.assertEqual(saved_demo["orders"], app.DEMO)
        self.assertEqual(saved_demo["source"], "demo")

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
