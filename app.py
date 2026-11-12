# -*- coding: utf-8 -*-
"""淘宝订单导出工具 - 本地 Web 服务"""

import copy
import json
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

BASE = Path(__file__).resolve().parent
OUT_DIR = BASE / "out"
DATA_DIR = BASE / "data"
OUT_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
LEGACY_LATEST = DATA_DIR / "latest.json"
DATA_FILES = {
    "orders": DATA_DIR / "orders.json",
    "cart": DATA_DIR / "cart.json",
    "demo": DATA_DIR / "demo.json",
}

__version__ = "1.1.0"
app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------------------------------------------------------------- 字段定义
FIELDS = [
    {"key": "seq", "label": "序号", "width": 6, "type": "int", "group": "基础"},
    {"key": "title", "label": "商品名称", "width": 46, "type": "text", "group": "基础"},
    {"key": "spec", "label": "规格/型号", "width": 22, "type": "text", "group": "基础"},
    {"key": "qty", "label": "数量", "width": 8, "type": "int", "group": "基础"},
    {"key": "price", "label": "页面单价", "width": 12, "type": "money", "group": "基础"},
    {"key": "originalPrice", "label": "划线价", "width": 12, "type": "money", "group": "基础"},
    {"key": "subtotal", "label": "小计", "width": 12, "type": "money", "group": "基础"},
    {"key": "shop", "label": "店铺", "width": 22, "type": "text", "group": "基础"},
    {"key": "url", "label": "商品链接", "width": 38, "type": "link", "group": "基础"},
    {"key": "checked", "label": "购物车已勾选", "width": 12, "type": "text", "group": "基础"},
    {"key": "orderNo", "label": "订单号", "width": 22, "type": "text", "group": "订单"},
    {"key": "orderTime", "label": "下单时间", "width": 20, "type": "text", "group": "订单"},
    {"key": "paid", "label": "订单实付", "width": 12, "type": "money", "group": "订单"},
    {"key": "status", "label": "订单状态", "width": 12, "type": "text", "group": "订单"},
    {"key": "raw", "label": "原始文本", "width": 60, "type": "text", "group": "调试"},
]
DEFAULT_KEYS = ["seq", "title", "spec", "qty", "price", "subtotal", "shop", "url"]
FIELD_MAP = {f["key"]: f for f in FIELDS}

STORE = {"orders": [], "scrapedAt": None, "source": "orders"}
JOB = {"running": False, "logs": [], "done": False, "error": None}
JOB_LOCK = threading.Lock()


# ---------------------------------------------------------------- 数据
def flatten(orders):
    items = []
    for oi, o in enumerate(orders):
        for ii, it in enumerate(o.get("items", [])):
            row = dict(it)
            row.update({
                "id": f"{oi}-{ii}",
                "orderIndex": oi,
                "orderNo": o.get("orderNo", ""),
                "orderTime": o.get("orderTime", ""),
                "shop": it.get("shop") or o.get("shop", ""),
                "paid": o.get("paid"),
                "status": o.get("status", ""),
            })
            items.append(row)
    return items


def load_store():
    """载入最近使用的数据集；订单、购物车分开保存，旧版 latest.json 仅用于迁移。"""
    snapshots = []
    for source in ("orders", "cart"):
        path = DATA_FILES[source]
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            snapshots.append((data.get("scrapedAt") or "", source, data))
        except (OSError, ValueError, TypeError):
            continue

    if snapshots:
        _, source, data = max(snapshots, key=lambda x: x[0])
    elif LEGACY_LATEST.exists():
        try:
            data = json.loads(LEGACY_LATEST.read_text(encoding="utf-8"))
            source = data.get("source", "orders")
        except (OSError, ValueError, TypeError):
            return
    else:
        return

    STORE["orders"] = data.get("orders", [])
    STORE["scrapedAt"] = data.get("scrapedAt")
    STORE["source"] = source if source in DATA_FILES else "orders"


def save_store():
    source = STORE["source"] if STORE["source"] in DATA_FILES else "orders"
    DATA_FILES[source].write_text(json.dumps(
        {"orders": STORE["orders"], "scrapedAt": STORE["scrapedAt"], "source": source},
        ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 路由
@app.get("/")
def index():
    return send_from_directory(BASE / "templates", "index.html")


@app.get("/api/state")
def state():
    items = flatten(STORE["orders"])
    return jsonify({
        "hasData": bool(items),
        "scrapedAt": STORE["scrapedAt"],
        "source": STORE["source"],
        "orderCount": len(STORE["orders"]),
        "itemCount": len(items),
        "version": __version__,
        "fields": FIELDS,
        "defaultKeys": DEFAULT_KEYS,
        "job": {"running": JOB["running"], "done": JOB["done"], "error": JOB["error"]},
    })


@app.get("/api/data")
def data():
    return jsonify({"items": flatten(STORE["orders"]), "scrapedAt": STORE["scrapedAt"]})


@app.post("/api/items")
def update_items():
    """前端编辑（规格/数量/单价）回写，保证导出与界面一致。"""
    body = request.get_json(silent=True) or {}
    patch = body.get("patch", {})
    if not isinstance(patch, dict):
        return jsonify({"ok": False, "msg": "patch 必须是对象"}), 400
    items = {it["id"]: it for it in flatten(STORE["orders"])}
    for iid, changes in patch.items():
        if iid not in items:
            continue
        it = items[iid]
        o = STORE["orders"][it["orderIndex"]]
        ii = int(iid.split("-")[1])
        for k in ("spec", "qty", "price", "title"):
            if k in changes:
                o["items"][ii][k] = changes[k]
    save_store()
    return jsonify({"ok": True})


@app.post("/api/scrape")
def scrape():
    body = request.get_json(silent=True) or {}
    source = body.get("source", "orders")
    if source not in ("orders", "cart"):
        return jsonify({"ok": False, "msg": "不支持的数据来源"}), 400
    try:
        max_pages = min(max(int(body.get("maxPages", 200)), 1), 500)
        wait = min(max(int(body.get("wait", 600)), 30), 1800)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "页数或等待时间格式不正确"}), 400
    debug = bool(body.get("debug", False))

    with JOB_LOCK:
        if JOB["running"]:
            return jsonify({"ok": False, "msg": "抓取任务进行中"}), 400
        JOB.update({"running": True, "logs": [], "done": False, "error": None})

    def log(msg):
        JOB["logs"].append(f"{datetime.now():%H:%M:%S}  {msg}")

    def work():
        try:
            import scraper
            log("启动抓取…")
            orders = scraper.run_scrape(source=source, max_pages=max_pages, wait=wait,
                                        debug=debug, log=log)
            STORE["orders"] = orders
            STORE["source"] = source
            STORE["scrapedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            save_store()
            JOB["done"] = True
        except Exception as e:
            JOB["error"] = str(e)
            log(f"失败：{e}")
        finally:
            JOB["running"] = False

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True})


@app.get("/api/scrape/status")
def scrape_status():
    return jsonify({
        "running": JOB["running"], "done": JOB["done"], "error": JOB["error"],
        "logs": JOB["logs"][-200:],
        "orderCount": len(STORE["orders"]),
    })


DEMO = [
    {
        "orderNo": "DEMO-2026-0001", "orderTime": "2026-10-26 10:15:00",
        "shop": "演示射频器材社（虚构）", "paid": 114.0, "status": "交易成功（演示）",
        "raw": "完全虚构演示数据，非真实订单",
        "items": [
            {"title": "SMA 同轴射频电缆组件", "spec": "SMA公-SMA公；RG316；长度1 m；50 Ω",
             "price": 28.5, "qty": 4, "url": "https://example.invalid/demo/RF-SMA-001", "raw": ""},
        ],
    },
    {
        "orderNo": "DEMO-2026-0002", "orderTime": "2026-10-21 14:20:00",
        "shop": "样例射频配件铺（虚构）", "paid": 59.6, "status": "交易成功（演示）",
        "raw": "完全虚构演示数据，非真实订单",
        "items": [
            {"title": "SMA射频连接线", "spec": "",
             "price": 29.8, "qty": 2, "url": "https://example.invalid/demo/RF-SMA-002", "raw": ""},
        ],
    },
    {
        "orderNo": "DEMO-2026-0003", "orderTime": "2026-10-18 09:05:00",
        "shop": "虚构真空科技铺", "paid": 748.0, "status": "交易成功（演示）",
        "raw": "完全虚构演示数据，非真实订单",
        "items": [
            {"title": "真空 KF 中心支架密封圈", "spec": "KF25；铝外圈+氟橡胶O圈",
             "price": 6.8, "qty": 10, "url": "https://example.invalid/demo/VAC-KF25-001", "raw": ""},
            {"title": "真空计电阻规管", "spec": "",
             "price": 680.0, "qty": 1, "url": "https://example.invalid/demo/VAC-GAUGE-001", "raw": ""},
        ],
    },
    {
        "orderNo": "DEMO-2026-0004", "orderTime": "2026-10-12 16:40:00",
        "shop": "示例光学工坊（虚构）", "paid": 346.0, "status": "交易成功（演示）",
        "raw": "完全虚构演示数据，非真实订单",
        "items": [
            {"title": "平凸透镜", "spec": "直径25.4 mm；焦距50 mm；K9；未镀膜",
             "price": 45.0, "qty": 2, "url": "https://example.invalid/demo/OPT-LENS-001", "raw": ""},
            {"title": "光学调整架", "spec": "1英寸镜片；两维调节；M4安装孔",
             "price": 128.0, "qty": 2, "url": "https://example.invalid/demo/OPT-MOUNT-001", "raw": ""},
        ],
    },
    {
        "orderNo": "DEMO-2026-0005", "orderTime": "2026-10-08 11:30:00",
        "shop": "模拟机械配件库（虚构）", "paid": 178.2, "status": "交易成功（演示）",
        "raw": "完全虚构演示数据，非真实订单",
        "items": [
            {"title": "欧标铝型材", "spec": "2020；长度500 mm；阳极氧化",
             "price": 18.5, "qty": 6, "url": "https://example.invalid/demo/MECH-2020-001", "raw": ""},
            {"title": "内六角圆柱头螺钉", "spec": "M5×12；304不锈钢；50只/盒",
             "price": 16.8, "qty": 4, "url": "https://example.invalid/demo/MECH-M5-001", "raw": ""},
        ],
    },
    {
        "orderNo": "DEMO-2026-0006", "orderTime": "2026-09-28 13:45:00",
        "shop": "模拟机械配件库（虚构）", "paid": 15.9, "status": "交易成功（演示）",
        "raw": "完全虚构演示数据，非真实订单",
        "items": [
            {"title": "内六角螺丝 50只装", "spec": "M5×12；304；50只",
             "price": 15.9, "qty": 1, "url": "https://example.invalid/demo/MECH-M5-002", "raw": ""},
        ],
    },
    {
        "orderNo": "DEMO-2026-0007", "orderTime": "2026-09-20 08:50:00",
        "shop": "样例办公用品店（虚构）", "paid": 257.7, "status": "交易成功（演示）",
        "raw": "完全虚构演示数据，非真实订单",
        "items": [
            {"title": "A4 复印纸", "spec": "80 g/m²；500张/包；白色",
             "price": 22.8, "qty": 10, "url": "https://example.invalid/demo/OFF-A4-001", "raw": ""},
            {"title": "黑色长尾夹", "spec": "32 mm；12只/盒",
             "price": 9.9, "qty": 3, "url": "https://example.invalid/demo/OFF-CLIP-001", "raw": ""},
        ],
    },
]


@app.post("/api/demo")
def demo():
    STORE["orders"] = copy.deepcopy(DEMO)
    STORE["source"] = "demo"
    STORE["scrapedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "（演示）"
    save_store()
    return jsonify({"ok": True, "itemCount": len(flatten(DEMO))})


# ---------------------------------------------------------------- 导出
def safe_text(value):
    """避免文本被 Excel 当作公式执行；不影响数值列。"""
    if value is None:
        return ""
    text = str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def item_subtotal(item):
    try:
        return round(float(item.get("price") or 0) * float(item.get("qty") or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def cell_value(key, item, seq):
    if key == "seq":
        return seq
    if key == "checked":
        return "是" if item.get("checked") else "否"
    if key == "subtotal":
        return item_subtotal(item)
    value = item.get(key, "")
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else safe_text(value)


@app.post("/api/export")
def export():
    body = request.get_json(silent=True) or {}
    ids = body.get("ids", [])
    fields = body.get("fields") or [{"key": k, "label": FIELD_MAP[k]["label"]} for k in DEFAULT_KEYS]
    fmt = body.get("format", "xlsx")
    if not isinstance(ids, list) or not isinstance(fields, list):
        return jsonify({"ok": False, "msg": "导出参数格式不正确"}), 400
    if fmt not in ("xlsx", "csv"):
        return jsonify({"ok": False, "msg": "仅支持 xlsx 或 csv"}), 400
    fields = [f for f in fields if isinstance(f, dict) and f.get("key") in FIELD_MAP]

    items = {it["id"]: it for it in flatten(STORE["orders"])}
    selected = [items[i] for i in ids if isinstance(i, str) and i in items]
    if not selected:
        return jsonify({"ok": False, "msg": "未选择任何商品"}), 400
    if not fields:
        return jsonify({"ok": False, "msg": "未选择任何有效字段"}), 400

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    headers = [safe_text(f.get("label") or FIELD_MAP[f["key"]]["label"]) for f in fields]
    rows = [[cell_value(f["key"], it, n) for f in fields] for n, it in enumerate(selected, 1)]

    total = round(sum(item_subtotal(it) for it in selected), 2)

    if fmt == "csv":
        import csv
        path = OUT_DIR / f"淘宝商品明细_{ts}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as fp:
            w = csv.writer(fp)
            w.writerow(headers)
            w.writerows(rows)
    else:
        path = OUT_DIR / f"淘宝商品明细_{ts}.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.title = "商品明细"
        ws.append(headers)

        head_fill = PatternFill("solid", fgColor="1F2937")
        thin = Side(style="thin", color="E5E7EB")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF", size=11)
            c.fill = head_fill
            c.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 24

        for r in rows:
            ws.append(r)

        for ci, f in enumerate(fields, 1):
            meta = FIELD_MAP.get(f["key"], {})
            ws.column_dimensions[get_column_letter(ci)].width = meta.get("width", 18)
            t = meta.get("type")
            for ri in range(2, ws.max_row + 1):
                cell = ws.cell(row=ri, column=ci)
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=(t == "text" and f["key"] in ("title", "raw")))
                if t == "money" and isinstance(cell.value, (int, float)):
                    cell.number_format = "#,##0.00"
                if t == "link" and cell.value:
                    cell.hyperlink = cell.value
                    cell.font = Font(color="2563EB", underline="single")

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

        # 汇总行
        if any(f["key"] == "subtotal" for f in fields):
            ws.append([])
            summary = [""] * len(fields)
            summary[0] = f"合计（{len(selected)} 项）"
            si = [f["key"] for f in fields].index("subtotal")
            summary[si] = round(total, 2)
            ws.append(summary)
            for c in ws[ws.max_row]:
                c.font = Font(bold=True)
            ws.cell(row=ws.max_row, column=si + 1).number_format = "#,##0.00"

        wb.save(path)

    return jsonify({
        "ok": True, "name": path.name, "path": str(path),
        "count": len(selected), "total": round(total, 2),
    })


@app.get("/api/download")
def download():
    name = request.args.get("name", "")
    if "/" in name or "\\" in name or ".." in name:
        return "bad name", 400
    return send_from_directory(OUT_DIR, name, as_attachment=True)


@app.post("/api/open")
def open_file():
    import os
    name = (request.json or {}).get("name", "")
    p = OUT_DIR / Path(name).name
    if p.exists():
        os.startfile(p)
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


SERVER = None
URL = "http://127.0.0.1:8765"


def shutdown_server():
    """停止 web 服务并退出进程（控制台 q 指令与网页按钮共用）。"""
    import os
    time.sleep(0.6)
    print("\n正在停止服务…", flush=True)
    try:
        if SERVER:
            SERVER.shutdown()
    except Exception:
        pass
    print("服务已停止。", flush=True)
    os._exit(0)


@app.post("/api/shutdown")
def shutdown():
    threading.Thread(target=shutdown_server, daemon=True).start()
    return jsonify({"ok": True, "msg": "服务已停止，可以关闭本页面"})


BANNER = r"""
============================================================
  淘宝订单导出工具   ·   服务运行中
------------------------------------------------------------
  界面地址   {url}
  导出目录   {out}
------------------------------------------------------------
  在下方输入指令，按回车执行：
    o  回车    重新打开工具界面
    q  回车    停止服务并退出（等同于点网页上的「停止服务」）
    h  回车    再次显示本说明

  直接关闭本窗口 = 立刻停止服务
  本窗口请勿最小化到遗忘，不用时输入 q 即可
============================================================
"""


def main():
    global SERVER
    import logging
    import socket
    import sys
    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", 8765)) == 0:
            print("端口 8765 已被占用。请先关闭旧窗口，或打开 http://127.0.0.1:8765 继续使用。", flush=True)
            return 1

    load_store()
    SERVER = make_server("127.0.0.1", 8765, app, threaded=True)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()

    print(BANNER.format(url=URL, out=OUT_DIR), flush=True)
    threading.Timer(1.0, lambda: webbrowser.open(URL)).start()

    # 无控制台（例如 pythonw 启动）时退化为普通阻塞运行
    if not sys.stdin or not sys.stdin.isatty():
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return

    try:
        while True:
            try:
                cmd = input().strip().lower()
            except EOFError:
                break
            if cmd in ("q", "quit", "exit", "stop"):
                shutdown_server()
                return
            if cmd in ("o", "open"):
                webbrowser.open(URL)
                print("已在浏览器打开界面。", flush=True)
            elif cmd in ("h", "help", "?"):
                print(BANNER.format(url=URL, out=OUT_DIR), flush=True)
            elif cmd:
                print("未知指令：输入 o 打开界面，q 停止服务，h 查看说明。", flush=True)
    except KeyboardInterrupt:
        shutdown_server()


if __name__ == "__main__":
    raise SystemExit(main())
