# -*- coding: utf-8 -*-
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scraper


class ScraperParsingTestCase(unittest.TestCase):
    def test_number_parsing(self):
        self.assertEqual(scraper._num("店铺优惠后\n￥\n1,299.50"), 1299.5)
        self.assertEqual(scraper._num(None), None)
        self.assertEqual(scraper._int("x12"), 12)
        self.assertEqual(scraper._int(""), None)

    def test_order_card_fields(self):
        card = {
            "orderNo": "1234567890", "orderTime": "2026-11-05", "shop": "测试店铺",
            "paid": "￥148.17", "status": "交易成功", "raw": "原始订单",
            "items": [{
                "title": "测试商品", "spec": "颜色：黑色", "price": "￥34.90",
                "originalPrice": "￥51.45", "qty": "x2", "url": "https://item.taobao.com/item.htm?id=1",
            }],
        }
        order = scraper.parse_order_card(card)
        self.assertEqual(order["orderNo"], "1234567890")
        self.assertEqual(order["paid"], 148.17)
        self.assertEqual(order["items"][0]["spec"], "颜色：黑色")
        self.assertEqual(order["items"][0]["price"], 34.9)
        self.assertEqual(order["items"][0]["originalPrice"], 51.45)
        self.assertEqual(order["items"][0]["qty"], 2)

    def test_empty_items_keep_order_fallback(self):
        order = scraper.parse_order_card({"raw": "兜底文本", "paid": "￥10", "items": []})
        self.assertEqual(len(order["items"]), 1)
        self.assertEqual(order["items"][0]["price"], 10)

    def test_stable_selectors_are_used(self):
        self.assertIn("trade-bought-list-order-container", scraper.EXTRACT_ORDERS_JS)
        self.assertIn("infoContent", scraper.EXTRACT_ORDERS_JS)
        self.assertIn("trade-cart-shop-container", scraper.EXTRACT_CART_JS)
        self.assertIn("trade-cart-item-info", scraper.EXTRACT_CART_JS)


if __name__ == "__main__":
    unittest.main()
