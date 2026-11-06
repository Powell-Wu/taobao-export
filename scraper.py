# -*- coding: utf-8 -*-
"""淘宝订单 / 购物车抓取模块。

订单页与购物车页均使用页面自带的稳定 class 前缀定位
（trade-bought-list-order-container / trade-cart-shop-container 等），
不依赖全文启发式匹配，避免把「最近浏览」「为你推荐」等模块误抓进来。
"""

import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent
PROFILE_DIR = BASE / ".browser-profile"
ORDER_URL = "https://buyertrade.taobao.com/trade/itemlist/list_bought_items.htm"
CART_URL = "https://cart.taobao.com/cart.htm"
PORT = 9222

CHROME_CANDIDATES = [
    os.environ.get("TAOBAO_EXPORT_BROWSER", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
_LOCAL_APP_DATA = os.environ.get("LOCALAPPDATA", "")
if _LOCAL_APP_DATA:
    CHROME_CANDIDATES.extend([
        os.path.join(_LOCAL_APP_DATA, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(_LOCAL_APP_DATA, r"Microsoft\Edge\Application\msedge.exe"),
    ])

STATUS_WORDS = [
    "等待买家付款", "等待卖家发货", "买家已付款", "卖家已发货", "等待买家确认收货",
    "交易成功", "交易关闭", "退款中", "退款成功", "退款失败", "已签收", "待评价",
]


def cdp_alive():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
        return True
    except Exception:
        return False


def launch_browser():
    if cdp_alive():
        return True
    exe = next((p for p in CHROME_CANDIDATES if os.path.exists(p)), None)
    if not exe:
        raise RuntimeError("未找到 Chrome 或 Edge")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [exe, f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE_DIR}",
         "--no-first-run", "--no-default-browser-check", "--start-maximized", "about:blank"],
        close_fds=True,
    )
    for _ in range(60):
        if cdp_alive():
            time.sleep(1)
            return True
        time.sleep(0.5)
    raise RuntimeError("浏览器调试端口启动失败")


# ================================================================ 订单页
EXTRACT_ORDERS_JS = r"""
() => {
  const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').trim();
  const txt = (el, sel) => { const e = el.querySelector(sel); return e ? norm(e.innerText) : ''; };

  const cards = [...document.querySelectorAll('.trade-bought-list-order-container')];
  return cards.map(card => {
    const idm = (card.id || '').match(/shopOrderContainer_(\d+)/);

    const items = [...card.querySelectorAll('[class*="trade-bought-list-order-info"]')].map(row => {
      const a = row.querySelector('a[href]');
      const priceCell = row.querySelector('[class*="itemInfoColPrice"]');
      const prices = priceCell
        ? [...priceCell.querySelectorAll('[class*="priceWrap"]')].map(e => norm(e.innerText))
        : [];
      // 规格在 infoContent 里；titleSubContainer 只是商品名的重复，不能用
      const specEl = row.querySelector('[class*="infoContent"]');
      return {
        title: txt(row, '[class*="titleText--"]') || txt(row, '[class*="titleContainer"]')
               || (a ? norm(a.innerText) : ''),
        spec: specEl ? norm(specEl.innerText) : '',
        price: prices[0] || (priceCell ? norm(priceCell.innerText) : ''),
        originalPrice: prices[1] || '',
        qty: txt(row, '[class*="quantity--"]'),
        url: a ? a.href : '',
        raw: norm(row.innerText).replace(/\n/g, ' / '),
      };
    });

    return {
      orderNo: idm ? idm[1] : txt(card, '[class*="shopInfoOrderId"]').replace(/[^\d]/g, ''),
      orderTime: txt(card, '[class*="shopInfoOrderTime"]'),
      shop: txt(card, '[class*="shopInfoName--"]') || txt(card, '[class*="shopInfoNameContainer"]'),
      status: txt(card, '[class*="shopInfoStatus"]'),
      paid: txt(card, '[class*="priceReal--"]'),
      raw: norm(card.innerText).replace(/\n/g, ' / '),
      items: items,
    };
  });
}
"""

# 结构变更时的兜底：纯文本启发式
EXTRACT_ORDERS_FALLBACK_JS = r"""
() => {
  const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').trim();
  const cand = [];
  document.querySelectorAll('div, li, section, article').forEach(el => {
    const t = el.innerText || '';
    if (t.length < 20 || t.length > 6000) return;
    if (t.indexOf('订单号') === -1) return;
    if (!/(实付|成交|等待买家|交易成功|交易关闭|退款)/.test(t)) return;
    cand.push(el);
  });
  cand.sort((a, b) => a.innerText.length - b.innerText.length);
  const kept = [];
  for (const el of cand) {
    if (kept.some(k => el.contains(k))) continue;
    kept.push(el);
  }
  const seen = new Set(), out = [];
  for (const el of kept) {
    const text = norm(el.innerText);
    if (seen.has(text)) continue;
    seen.add(text);
    const a = el.querySelector('a[href]');
    out.push({raw: text.replace(/\n/g, ' / '), items: [], url: a ? a.href : ''});
  }
  return out;
}
"""

# ================================================================ 购物车
EXTRACT_CART_JS = r"""
() => {
  const norm = s => (s || '').replace(/\u00a0/g, ' ').replace(/[ \t]+/g, ' ').trim();
  const shops = [...document.querySelectorAll('[class*="trade-cart-shop-container"]')];

  return shops.map(shopEl => {
    const nameEl = shopEl.querySelector('[class*="cartShopName"]');
    const items = [...shopEl.querySelectorAll('[class*="trade-cart-item-info"]')].map(row => {
      const a = row.querySelector('a[href]');
      const titleEl = row.querySelector('[class*="trade-cart-item-detail"] [class*="title"]');
      const skuEl = row.querySelector('[class*="trade-cart-item-sku-old"]');
      const priceCell = row.querySelector('[class*="trade-cart-item-price"]');
      const prices = priceCell
        ? [...priceCell.querySelectorAll('[class*="trade-price-container"]')].map(e => norm(e.innerText))
        : [];
      const qtyInput = row.querySelector('input[class*="inputWrapper"]');
      return {
        title: norm(titleEl ? titleEl.innerText : (a ? a.innerText : '')),
        spec: norm(skuEl ? skuEl.innerText : ''),
        price: prices[0] || '',
        originalPrice: prices[1] || '',
        qty: qtyInput ? qtyInput.value : '1',
        url: a ? a.href : '',
        checked: !!row.querySelector('.ant-checkbox-checked'),
        raw: norm(row.innerText).replace(/\n/g, ' / '),
      };
    });
    return {shop: nameEl ? norm(nameEl.innerText) : '', items: items};
  });
}
"""


# ================================================================ 解析
def _num(s):
    if s is None:
        return None
    m = re.search(r"([\d,]+(?:\.\d{1,2})?)", str(s))
    return float(m.group(1).replace(",", "")) if m else None


def _int(s):
    if s is None:
        return None
    m = re.search(r"(\d+)", str(s))
    return int(m.group(1)) if m else None


def _clean_item(it):
    return {
        "title": (it.get("title") or "").strip(),
        "spec": (it.get("spec") or "").strip(),
        "price": _num(it.get("price")),
        "originalPrice": _num(it.get("originalPrice")),
        "qty": _int(it.get("qty")) or 1,
        "url": it.get("url") or "",
        "checked": bool(it.get("checked", False)),
        "raw": it.get("raw", ""),
    }


def parse_order_card(card):
    items = [_clean_item(it) for it in card.get("items", [])]
    items = [it for it in items if it["title"]]
    if not items:
        items = [{"title": card.get("raw", "")[:120], "spec": "", "price": _num(card.get("paid")),
                  "qty": 1, "url": "", "checked": False, "raw": card.get("raw", "")}]
    return {
        "orderNo": card.get("orderNo", ""),
        "orderTime": card.get("orderTime", ""),
        "shop": card.get("shop", ""),
        "paid": _num(card.get("paid")),
        "status": card.get("status", ""),
        "raw": card.get("raw", ""),
        "items": items,
    }


def parse_order_fallback(card):
    text = card.get("raw", "")
    m = re.search(r"订单号[：:\s]*(\d{8,})", text)
    order_no = m.group(1) if m else ""
    m = re.search(r"实付款?[：:\s]*[¥￥]?\s*([\d,]+(?:\.\d{1,2})?)", text)
    paid = m.group(1) if m else ""
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)", text)
    status = next((w for w in STATUS_WORDS if w in text), "")
    return {
        "orderNo": order_no, "orderTime": m.group(1) if m else "", "shop": "",
        "paid": _num(paid), "status": status, "raw": text,
        "items": [{"title": text[:120], "spec": "", "price": _num(paid), "qty": 1,
                   "url": card.get("url", ""), "checked": False, "raw": text}],
    }


# ================================================================ 流程
def first_order_no(page):
    try:
        return page.evaluate(
            "() => { const m = document.body.innerText.match(/订单号[：:\\s]*(\\d{8,})/); return m ? m[1] : ''; }")
    except Exception:
        return ""


def goto_next_page(page):
    for sel in ['a:has-text("下一页")', 'button:has-text("下一页")', '.pagination-next',
                '[class*="pagination"] [class*="next"]']:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            cls = (loc.get_attribute("class") or "").lower()
            if "disabled" in cls or loc.get_attribute("aria-disabled") == "true":
                return False
            loc.click(timeout=5000)
            return True
        except Exception:
            continue
    return False


def wait_ready(page, ready_js, wait, log):
    log("等待登录 / 页面就绪…（如需登录请在浏览器窗口扫码）")
    start = time.time()
    while time.time() - start < wait:
        try:
            if page.evaluate(ready_js):
                return True
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError("等待页面就绪超时（可能未登录）")


def scrape_orders(page, max_pages, debug, log):
    orders, seen = [], set()
    page_no = 1
    while page_no <= max_pages:
        try:
            page.wait_for_selector(".trade-bought-list-order-container", timeout=15000)
        except Exception:
            log("未检测到订单卡片，停止翻页")
            break
        page.wait_for_timeout(1200)
        if debug and page_no == 1:
            (BASE / "debug_page1.html").write_text(page.content(), encoding="utf-8")

        cards = page.evaluate(EXTRACT_ORDERS_JS)
        mode = "精确"
        if not cards:
            cards = page.evaluate(EXTRACT_ORDERS_FALLBACK_JS)
            mode = "兜底"

        new_cnt = 0
        for c in cards:
            o = parse_order_card(c) if mode == "精确" else parse_order_fallback(c)
            key = o["orderNo"] or o["raw"][:60]
            if key in seen:
                continue
            seen.add(key)
            orders.append(o)
            new_cnt += 1
        log(f"第 {page_no} 页（{mode}解析）：{len(cards)} 单，新增 {new_cnt}，累计 {len(orders)}")

        if not cards or new_cnt == 0:
            break
        before = first_order_no(page)
        if not goto_next_page(page):
            log("已到最后一页")
            break
        for _ in range(30):
            page.wait_for_timeout(500)
            now = first_order_no(page)
            if now and now != before:
                break
        page_no += 1
    return orders


def scrape_cart(page, log):
    # 购物车懒加载：反复滚到底，直到商品数稳定
    prev, stable = -1, 0
    for _ in range(60):
        cnt = page.evaluate("document.querySelectorAll('[class*=\"trade-cart-item-info\"]').length")
        if cnt == prev:
            stable += 1
        else:
            stable = 0
            log(f"已加载 {cnt} 件商品…")
        if stable >= 3:
            break
        prev = cnt
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1200)
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(800)

    groups = page.evaluate(EXTRACT_CART_JS)
    orders = []
    for g in groups:
        items = [_clean_item(it) for it in g["items"]]
        items = [it for it in items if it["title"]]
        if not items:
            continue
        orders.append({
            "orderNo": "", "orderTime": "", "shop": g["shop"], "paid": None,
            "status": "购物车", "raw": "", "items": items,
        })
    log(f"购物车共 {len(orders)} 个店铺、{sum(len(o['items']) for o in orders)} 件商品")
    return orders


def run_scrape(source="orders", max_pages=200, wait=600, debug=False, log=print):
    launch_browser()
    log("浏览器已就绪")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        if source == "cart":
            page.goto(CART_URL, wait_until="domcontentloaded", timeout=60000)
            wait_ready(page,
                       "() => document.querySelectorAll('[class*=\"trade-cart-item-info\"]').length > 0",
                       wait, log)
            log("已进入购物车，开始加载全部商品")
            return scrape_cart(page, log)

        page.goto(ORDER_URL, wait_until="domcontentloaded", timeout=60000)
        wait_ready(page,
                   "() => document.querySelectorAll('.trade-bought-list-order-container').length > 0",
                   wait, log)
        log("已登录，开始抓取订单")
        orders = scrape_orders(page, max_pages, debug, log)
        log(f"抓取完成，共 {len(orders)} 个订单")
        return orders
