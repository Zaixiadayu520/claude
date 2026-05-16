"""
亚马逊新品采集器 — 图形界面（中文版）
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import json
import os
import sys
import queue
import logging
import time
from pathlib import Path
from datetime import datetime

import schedule as schedule_lib

BASE_DIR     = Path(__file__).parent
SETTINGS_FILE = BASE_DIR / "settings.json"
DATA_DIR     = BASE_DIR / "data"

DEFAULT_SETTINGS = {
    "sellers": [],
    "schedule_time": "08:00",
    "schedule_enabled": False,
    "listing_preset": "最近30天",
    "listing_date_start": "",
    "listing_date_end":   "",
    "fetch_listing_date": True,
    "proxy": "",   # HTTP/HTTPS代理，例如 http://127.0.0.1:7890
}

# ── 配色 ──────────────────────────────────────────────────────────────────────
BG      = "#1e1e2e"
BG2     = "#2a2a3e"
BG3     = "#313145"
ACCENT  = "#7c6af7"
ACCENT2 = "#5a4fcf"
GREEN   = "#50fa7b"
RED     = "#ff5555"
YELLOW  = "#f1fa8c"
CYAN    = "#8be9fd"
FG      = "#cdd6f4"
FG2     = "#a6adc8"
BORDER  = "#45475a"


# ── 设置持久化 ────────────────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ── 日志队列 ──────────────────────────────────────────────────────────────────

class QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record):
        self.q.put(self.format(record))


# ── 添加店铺弹窗 ──────────────────────────────────────────────────────────────

class AddSellerDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("添加店铺")
        self.resizable(False, False)
        self.configure(bg=BG2)
        self.grab_set()
        self.geometry(f"480x270+{parent.winfo_rootx()+140}+{parent.winfo_rooty()+140}")

        tk.Label(self, text="添加新店铺", font=("微软雅黑", 13, "bold"),
                 bg=BG2, fg=FG).pack(pady=(18, 6))

        # 提示：支持粘贴完整 URL
        tk.Label(self,
                 text="可直接粘贴店铺完整 URL，系统自动提取卖家 ID",
                 font=("微软雅黑", 8), bg=BG2, fg=FG2).pack()

        form = tk.Frame(self, bg=BG2)
        form.pack(padx=30, fill="x", pady=(10, 0))

        def _row(label, row):
            tk.Label(form, text=label, font=("微软雅黑", 9),
                     bg=BG2, fg=FG2).grid(row=row, column=0, sticky="w", pady=6)

        _row("卖家 ID / URL *", 0)
        self.id_var = tk.StringVar()
        id_entry = _entry(form, self.id_var, font=("Consolas", 10))
        id_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=6, ipady=4)
        id_entry.focus()

        _row("店铺名称", 1)
        self.name_var = tk.StringVar()
        _entry(form, self.name_var).grid(
            row=1, column=1, sticky="ew", padx=(10, 0), pady=6, ipady=4)

        # 解析预览
        self.preview = tk.Label(form, text="", font=("Consolas", 8),
                                bg=BG2, fg=CYAN)
        self.preview.grid(row=2, column=1, sticky="w", padx=(10, 0))
        self.id_var.trace_add("write", self._update_preview)

        form.columnconfigure(1, weight=1)

        self.err = tk.Label(self, text="", font=("微软雅黑", 8), bg=BG2, fg=RED)
        self.err.pack(pady=(4, 0))

        bf = tk.Frame(self, bg=BG2)
        bf.pack(pady=10)
        _btn(bf, "取消", self.destroy, bg=BG3).pack(side="left", padx=6)
        _btn(bf, "确认添加", self._ok, bg=ACCENT).pack(side="left", padx=6)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())

    def _extract_id(self, raw: str) -> str:
        import re
        m = re.search(r'[?&]me=([A-Z0-9]+)', raw, re.IGNORECASE)
        return m.group(1) if m else raw.strip()

    def _update_preview(self, *_):
        raw = self.id_var.get().strip()
        extracted = self._extract_id(raw)
        if extracted != raw and extracted:
            self.preview.config(text=f"→ 将使用卖家 ID：{extracted}")
        else:
            self.preview.config(text="")

    def _ok(self):
        raw = self.id_var.get().strip()
        if not raw:
            self.err.config(text="卖家 ID 不能为空")
            return
        sid = self._extract_id(raw)
        name = self.name_var.get().strip() or sid
        self.result = {"id": sid, "name": name}
        self.destroy()


# ── 翻译工具 ─────────────────────────────────────────────────────────────────

def _translate_to_zh(text: str) -> str:
    """Translate English text to Chinese via Google Translate (no API key needed)."""
    if not text:
        return ""
    try:
        import urllib.parse, urllib.request, json
        q   = urllib.parse.quote(text[:500])
        url = (f"https://translate.googleapis.com/translate_a/single"
               f"?client=gtx&sl=en&tl=zh-CN&dt=t&q={q}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode())
        return "".join(seg[0] for seg in data[0] if seg[0])
    except Exception:
        return ""


# ── 数据库查看弹窗 ────────────────────────────────────────────────────────────

class DBViewerDialog(tk.Toplevel):
    # Table columns: (db_field, header_label, width)
    COLS = [
        ("asin",                 "ASIN",       100),
        ("url",                  "商品链接",    80),
        ("title",                "标题",        220),
        ("title_zh",             "中文标题",    200),
        ("brand",                "品牌",        90),
        ("price",                "价格(USD)",   80),
        ("monthly_sales",        "月销量",      75),
        ("rating",               "评分",        58),
        ("review_count",         "评论数",      75),
        ("prime",                "Prime",       55),
        ("date_first_available", "上架日期",    100),
        ("seller_id",            "店铺ID",      120),
        ("scraped_date",         "采集日期",    90),
        ("is_new",               "新品",        45),
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("数据库数据查看")
        self.geometry("1200x660")
        self.minsize(1000, 520)
        self.configure(bg=BG)
        self.grab_set()
        self._zh_cache: dict[str, str] = {}
        self._img_cache: dict[str, object] = {}   # asin -> ImageTk.PhotoImage
        self._placeholder_img = None
        self._date_mode = tk.StringVar(value="scraped")   # "scraped" or "listed"
        self._build()
        self._apply_quick("最近30天")

    # ── 构建界面 ──────────────────────────────────────────────────────────────

    def _build(self):
        col_ids = [c[0] for c in self.COLS]

        # ── 行1：日期类型切换 + 快捷按钮 + 自定义范围 ────────────────────────
        row1 = tk.Frame(self, bg=BG2, highlightthickness=1,
                        highlightbackground=BORDER)
        row1.pack(fill="x", padx=12, pady=(10, 4))

        _label(row1, "  时间类型：", size=9, fg=FG2, bg=BG2).pack(
            side="left", padx=(6, 0), pady=8)
        for val, lbl in [("scraped", "按采集日期"), ("listed", "按上架日期")]:
            tk.Radiobutton(
                row1, text=lbl, variable=self._date_mode, value=val,
                font=("微软雅黑", 9), bg=BG2, fg=FG,
                selectcolor=BG3, activebackground=BG2,
                command=self._load,
            ).pack(side="left", padx=4)

        tk.Label(row1, text="|", bg=BG2, fg=BORDER,
                 font=("微软雅黑", 12)).pack(side="left", padx=6)

        self._quick_btns: dict[str, tk.Button] = {}
        for lbl in ["今日", "最近7天", "最近30天", "最近90天", "今年", "全部"]:
            b = tk.Button(row1, text=lbl, font=("微软雅黑", 9),
                          relief="flat", cursor="hand2",
                          padx=8, pady=4, bd=0, bg=BG3, fg=FG,
                          activebackground=ACCENT2, activeforeground="white",
                          command=lambda l=lbl: self._apply_quick(l))
            b.pack(side="left", padx=2, pady=6)
            self._quick_btns[lbl] = b

        tk.Label(row1, text="|", bg=BG2, fg=BORDER,
                 font=("微软雅黑", 12)).pack(side="left", padx=6)
        _label(row1, "自定义：", size=9, fg=FG2, bg=BG2).pack(side="left")
        self.start_var = tk.StringVar()
        self.end_var   = tk.StringVar()
        s_e = _entry(row1, self.start_var, font=("Consolas", 9))
        s_e.config(width=12); s_e.pack(side="left", ipady=3, padx=(2, 0))
        _label(row1, "~", size=9, fg=FG2, bg=BG2).pack(side="left", padx=3)
        e_e = _entry(row1, self.end_var, font=("Consolas", 9))
        e_e.config(width=12); e_e.pack(side="left", ipady=3)
        _label(row1, "(YYYY-MM-DD)", size=8, fg=FG2, bg=BG2).pack(
            side="left", padx=(4, 0))

        # ── 行2：关键词 + 操作按钮 ───────────────────────────────────────────
        row2 = tk.Frame(self, bg=BG)
        row2.pack(fill="x", padx=12, pady=(2, 6))

        _label(row2, "关键词：", size=9, fg=FG2, bg=BG).pack(side="left")
        self.filter_var = tk.StringVar()
        kw = _entry(row2, self.filter_var)
        kw.config(width=22); kw.pack(side="left", padx=6, ipady=3)
        _label(row2, "ASIN/标题/品牌", size=8, fg=FG2, bg=BG).pack(side="left")

        self.only_new = tk.BooleanVar()
        tk.Checkbutton(row2, text="仅看新品", variable=self.only_new,
                       bg=BG, fg=FG, selectcolor=BG3,
                       activebackground=BG, font=("微软雅黑", 9),
                       command=self._load).pack(side="left", padx=(10, 4))

        _btn(row2, "🔍 查询",    self._load,          bg=ACCENT).pack(side="left", padx=3)
        _btn(row2, "🌐 翻译选中", self._translate_selected, bg=BG3).pack(side="left", padx=3)
        _btn(row2, "🌐 翻译全部", self._translate_all,  bg=BG3).pack(side="left", padx=3)
        _btn(row2, "📤 导出CSV",  self._export_csv,     bg=BG3).pack(side="left", padx=3)

        self.count_label = tk.Label(row2, text="", font=("微软雅黑", 9),
                                    bg=BG, fg=FG2)
        self.count_label.pack(side="right", padx=10)

        # ── 表格 ──────────────────────────────────────────────────────────────
        frame = tk.Frame(self, bg=BG)
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.Treeview",
                        background=BG3, foreground=FG,
                        fieldbackground=BG3, rowheight=24,
                        font=("微软雅黑", 9))
        style.configure("Dark.Treeview.Heading",
                        background=BG2, foreground=FG,
                        font=("微软雅黑", 9, "bold"))
        style.map("Dark.Treeview",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])

        self.tree = ttk.Treeview(frame, columns=col_ids,
                                 show="tree headings", style="Dark.Treeview")
        self.tree.column("#0", width=60, minwidth=60, anchor="center", stretch=False)
        self.tree.heading("#0", text="主图")
        for cid, lbl, w in self.COLS:
            self.tree.heading(cid, text=lbl,
                              command=lambda c=cid: self._sort_by(c))
            anchor = "center" if cid in ("is_new", "prime", "url") else "w"
            self.tree.column(cid, width=w, anchor=anchor, minwidth=40)

        vsb = ttk.Scrollbar(frame, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        # 双击打开商品链接
        self.tree.bind("<Double-1>", self._on_double_click)

        self._sort_col = ""
        self._sort_asc = True
        self._rows_cache: list[dict] = []   # raw dicts for translation

    # ── 快捷日期 ──────────────────────────────────────────────────────────────

    def _apply_quick(self, label: str):
        from datetime import date, timedelta
        today = date.today()
        mapping = {
            "今日":    (today, today),
            "最近7天": (today - timedelta(days=6),  today),
            "最近30天":(today - timedelta(days=29), today),
            "最近90天":(today - timedelta(days=89), today),
            "今年":    (date(today.year, 1, 1),     today),
            "全部":    (date(2000, 1, 1),            today),
        }
        start, end = mapping.get(label, (date(2000, 1, 1), today))
        self.start_var.set(start.isoformat())
        self.end_var.set(end.isoformat())
        for lbl, btn in self._quick_btns.items():
            btn.config(bg=ACCENT if lbl == label else BG3,
                       fg="white" if lbl == label else FG)
        self._load()

    # ── 查询 ──────────────────────────────────────────────────────────────────

    def _load(self):
        try:
            import sqlite3
            from amazon_scraper.db import DB_PATH
            if not DB_PATH.exists():
                messagebox.showinfo("提示", "数据库尚未创建，请先运行一次采集。")
                return

            con = sqlite3.connect(str(DB_PATH))
            # Check which columns exist (migration safety)
            existing_cols = {r[1] for r in con.execute("PRAGMA table_info(products)")}

            keyword   = self.filter_var.get().strip()
            only_new  = self.only_new.get()
            start     = self.start_var.get().strip() or "2000-01-01"
            end       = self.end_var.get().strip()   or "2099-12-31"
            date_col  = ("date_first_available"
                         if self._date_mode.get() == "listed"
                         and "date_first_available" in existing_cols
                         else "scraped_date")

            conditions = [f"{date_col} BETWEEN ? AND ?"]
            params: list = [start, end]
            if keyword:
                conditions.append("(asin LIKE ? OR title LIKE ? OR brand LIKE ?)")
                params += [f"%{keyword}%"] * 3
            if only_new:
                conditions.append("is_new=1")

            # Build SELECT with only columns that exist
            safe = lambda c: c if c in existing_cols else f"'' AS {c}"
            sel = ", ".join(
                c if c in existing_cols or c == "url" else f"'' AS {c}"
                for c in ("asin","url","title","brand","price","list_price",
                          "rating","review_count","prime","sponsored",
                          "monthly_sales","date_first_available",
                          "main_image_url","scraped_date","is_new","seller_id")
                if c in existing_cols or c not in existing_cols
            )
            # Simpler: just select what we need
            db_cols = ["asin","url","title","brand","price",
                       "monthly_sales","rating","review_count","prime",
                       "date_first_available","seller_id","scraped_date","is_new",
                       "main_image_url"]
            select_parts = [c if c in existing_cols else f"'' AS {c}"
                            for c in db_cols]
            where = "WHERE " + " AND ".join(conditions)
            sql = (f"SELECT {', '.join(select_parts)} FROM products {where} "
                   f"ORDER BY {date_col} DESC, id DESC LIMIT 5000")
            rows = con.execute(sql, params).fetchall()
            con.close()

            self._rows_cache = [dict(zip(db_cols, r)) for r in rows]
            self.tree.delete(*self.tree.get_children())
            thumb_jobs: list[tuple[str, str, str]] = []   # (iid, url, asin)
            for rd in self._rows_cache:
                zh = self._zh_cache.get(rd["asin"], "")
                vals = (
                    rd["asin"], "🔗 打开", rd["title"], zh,
                    rd["brand"], rd["price"],
                    rd["monthly_sales"], rd["rating"], rd["review_count"],
                    rd["prime"], rd["date_first_available"],
                    rd["seller_id"], rd["scraped_date"],
                    "✓" if str(rd["is_new"]) == "1" else "",
                )
                tag = "new" if str(rd["is_new"]) == "1" else ""
                # Use cached image if available
                cached_img = self._img_cache.get(rd["asin"])
                iid = self.tree.insert("", "end",
                                       image=cached_img or "",
                                       values=vals, tags=(tag,))
                if not cached_img and rd.get("main_image_url"):
                    thumb_jobs.append((iid, rd["main_image_url"], rd["asin"]))
            self.tree.tag_configure("new", foreground=GREEN)
            if thumb_jobs:
                threading.Thread(
                    target=self._load_thumbnails,
                    args=(thumb_jobs,), daemon=True).start()
            self.count_label.config(
                text=f"共 {len(rows)} 条  [{start} ~ {end}]  ({date_col})")
        except Exception as exc:
            messagebox.showerror("查询失败", str(exc))

    # ── 双击打开链接 ──────────────────────────────────────────────────────────

    def _on_double_click(self, event):
        item = self.tree.focus()
        if not item:
            return
        col = self.tree.identify_column(event.x)
        col_idx = int(col.replace("#", "")) - 1
        col_id = [c[0] for c in self.COLS][col_idx] if col_idx < len(self.COLS) else ""
        row_vals = self.tree.item(item)["values"]
        # Find URL from cache
        iid_list = list(self.tree.get_children())
        row_idx  = iid_list.index(item) if item in iid_list else -1
        if row_idx >= 0 and row_idx < len(self._rows_cache):
            url = self._rows_cache[row_idx].get("url", "")
            if url:
                import webbrowser
                webbrowser.open(url)

    # ── 翻译 ──────────────────────────────────────────────────────────────────

    def _translate_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一行。")
            return
        self._do_translate(sel)

    def _translate_all(self):
        self._do_translate(self.tree.get_children())

    def _do_translate(self, item_ids):
        iid_list = list(self.tree.get_children())
        to_fetch: list[tuple[str, str, int]] = []  # (iid, title, row_idx)
        for iid in item_ids:
            idx = iid_list.index(iid) if iid in iid_list else -1
            if idx < 0 or idx >= len(self._rows_cache):
                continue
            rd = self._rows_cache[idx]
            asin  = rd["asin"]
            title = rd["title"]
            if title and asin not in self._zh_cache:
                to_fetch.append((iid, title, asin))

        if not to_fetch:
            # Already translated — just refresh display
            self._refresh_zh_column()
            return

        self.count_label.config(text=f"翻译中… 0/{len(to_fetch)}")
        self.update_idletasks()

        def worker():
            for i, (iid, title, asin) in enumerate(to_fetch, 1):
                zh = _translate_to_zh(title)
                self._zh_cache[asin] = zh
                self.after(0, lambda iid=iid, zh=zh:
                           self._update_zh_cell(iid, zh))
                self.after(0, lambda i=i:
                           self.count_label.config(
                               text=f"翻译中… {i}/{len(to_fetch)}"))
            self.after(0, lambda: self.count_label.config(
                text=f"翻译完成，共 {len(self._rows_cache)} 条"))

        threading.Thread(target=worker, daemon=True).start()

    def _update_zh_cell(self, iid: str, zh: str):
        vals = list(self.tree.item(iid)["values"])
        # title_zh is column index 3
        if len(vals) > 3:
            vals[3] = zh
            self.tree.item(iid, values=vals)

    def _refresh_zh_column(self):
        iid_list = list(self.tree.get_children())
        for idx, iid in enumerate(iid_list):
            if idx >= len(self._rows_cache):
                break
            asin = self._rows_cache[idx]["asin"]
            zh   = self._zh_cache.get(asin, "")
            if zh:
                self._update_zh_cell(iid, zh)

    # ── 列排序 ────────────────────────────────────────────────────────────────

    def _sort_by(self, col: str):
        rows = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        asc = not self._sort_asc if self._sort_col == col else True
        rows.sort(key=lambda x: x[0], reverse=not asc)
        for idx, (_, k) in enumerate(rows):
            self.tree.move(k, "", idx)
        self._sort_col = col
        self._sort_asc = asc

    # ── 缩略图加载 ────────────────────────────────────────────────────────────

    def _load_thumbnails(self, jobs: list[tuple[str, str, str]]):
        """Download and set thumbnails for treeview items (background thread)."""
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            futs = {pool.submit(self._load_one_thumb, iid, url, asin): iid
                    for iid, url, asin in jobs}
            for _ in concurrent.futures.as_completed(futs):
                pass

    def _load_one_thumb(self, iid: str, url: str, asin: str):
        if asin in self._img_cache or not url:
            return
        try:
            from PIL import Image, ImageTk
            import urllib.request, io
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = r.read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((52, 52), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._img_cache[asin] = photo
            self.after(0, lambda iid=iid, photo=photo:
                       self._set_thumb(iid, photo))
        except Exception:
            pass

    def _set_thumb(self, iid: str, photo):
        try:
            self.tree.item(iid, image=photo)
        except Exception:
            pass

    # ── 导出 CSV ──────────────────────────────────────────────────────────────

    def _export_csv(self):
        from tkinter import filedialog
        import csv
        from datetime import date
        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
            initialfile=f"export_{date.today().isoformat()}.csv",
        )
        if not path:
            return
        headers = [c[1] for c in self.COLS]
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(headers)
                for iid in self.tree.get_children():
                    w.writerow(self.tree.item(iid)["values"])
            messagebox.showinfo("导出成功", f"已保存至：\n{path}")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))


# ── 通用小控件 ────────────────────────────────────────────────────────────────

def _btn(parent, text, command, bg=BG3, fg=FG):
    return tk.Button(parent, text=text, command=command,
                     bg=bg, fg=fg, font=("微软雅黑", 9, "bold"),
                     relief="flat", cursor="hand2",
                     padx=12, pady=5,
                     activebackground=ACCENT2, activeforeground="white", bd=0)


def _entry(parent, textvariable, font=("微软雅黑", 10)):
    return tk.Entry(parent, textvariable=textvariable, font=font,
                    bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                    highlightthickness=1, highlightcolor=ACCENT,
                    highlightbackground=BORDER)


def _label(parent, text, size=9, bold=False, fg=FG, bg=None):
    return tk.Label(parent, text=text,
                    font=("微软雅黑", size, "bold" if bold else "normal"),
                    fg=fg, bg=bg or parent["bg"])


# ── 主应用 ────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("亚马逊新品采集器")
        self.geometry("960x660")
        self.minsize(820, 580)
        self.configure(bg=BG)

        self.settings = load_settings()
        self.log_queue: queue.Queue = queue.Queue()
        self._scraper_thread: threading.Thread | None = None
        self._scheduler_thread: threading.Thread | None = None
        self._scheduler_running = False

        self._setup_logging()
        self._build_ui()
        self._refresh_seller_list()
        self._poll_log()
        self._refresh_stats()

        if self.settings.get("schedule_enabled"):
            self._start_schedule(silent=True)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 日志 ──────────────────────────────────────────────────────────────────

    def _setup_logging(self):
        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.handlers.clear()
        root.addHandler(handler)

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.after(300, self._poll_log)

    def _append_log(self, msg: str):
        self.log_text.configure(state="normal")
        if "[ERROR]" in msg:
            tag = "error"
        elif "[WARNING]" in msg:
            tag = "warn"
        elif "完成" in msg or "新产品" in msg:
            tag = "success"
        else:
            tag = "info"
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ── 构建界面 ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # 顶栏
        header = tk.Frame(self, bg=BG2, height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="亚马逊  新品采集器",
                 font=("微软雅黑", 14, "bold"), bg=BG2, fg=FG).pack(
            side="left", padx=20, pady=12)
        self.status_dot = tk.Label(header, text="● 待机",
                                   font=("微软雅黑", 9), bg=BG2, fg=FG2)
        self.status_dot.pack(side="right", padx=20)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(10, 6))

        # 左侧店铺面板
        left = tk.Frame(body, bg=BG2,
                        highlightthickness=1, highlightbackground=BORDER)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)
        left.configure(width=265)
        self._build_seller_panel(left)

        # 右侧主面板
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        self._build_right_panel(right)

    def _build_seller_panel(self, parent):
        _label(parent, "  店铺管理", size=10, bold=True, bg=BG2).pack(
            anchor="w", pady=(14, 6))

        lf = tk.Frame(parent, bg=BG2)
        lf.pack(fill="both", expand=True, padx=10)

        sb = tk.Scrollbar(lf, bg=BG3, troughcolor=BG2, relief="flat", bd=0)
        sb.pack(side="right", fill="y")

        self.seller_lb = tk.Listbox(
            lf, yscrollcommand=sb.set,
            bg=BG3, fg=FG,
            selectbackground=ACCENT, selectforeground="white",
            font=("微软雅黑", 10), relief="flat", bd=0,
            highlightthickness=0, activestyle="none",
        )
        self.seller_lb.pack(fill="both", expand=True)
        sb.config(command=self.seller_lb.yview)

        bf = tk.Frame(parent, bg=BG2)
        bf.pack(fill="x", padx=10, pady=10)
        _btn(bf, "+ 添加店铺", self._add_seller, bg=ACCENT).pack(fill="x", pady=(0, 5))
        _btn(bf, "✕ 删除选中", self._remove_seller).pack(fill="x")

    def _build_right_panel(self, parent):
        # 统计卡片
        stats_card = tk.Frame(parent, bg=BG2,
                              highlightthickness=1, highlightbackground=BORDER)
        stats_card.pack(fill="x", pady=(0, 8))
        _label(stats_card, "  数据统计", size=10, bold=True, bg=BG2).pack(
            anchor="w", padx=4, pady=(10, 6))

        sf = tk.Frame(stats_card, bg=BG2)
        sf.pack(fill="x", padx=14, pady=(0, 12))

        self.stat_vars = {}
        stats_def = [
            ("total",   "累计产品数",  CYAN),
            ("new",     "今日新品",    GREEN),
            ("sellers", "监控店铺数",  YELLOW),
            ("last",    "最后采集日期", FG2),
        ]
        for key, label, color in stats_def:
            box = tk.Frame(sf, bg=BG3, padx=14, pady=8)
            box.pack(side="left", padx=(0, 8), fill="y")
            var = tk.StringVar(value="—")
            self.stat_vars[key] = var
            tk.Label(box, textvariable=var, font=("微软雅黑", 16, "bold"),
                     bg=BG3, fg=color).pack()
            _label(box, label, size=8, fg=FG2, bg=BG3).pack()

        # 控制卡片
        ctrl_card = tk.Frame(parent, bg=BG2,
                             highlightthickness=1, highlightbackground=BORDER)
        ctrl_card.pack(fill="x", pady=(0, 8))
        _label(ctrl_card, "  采集设置", size=10, bold=True, bg=BG2).pack(
            anchor="w", padx=4, pady=(10, 4))

        # ── 行1：上架时间选择 ─────────────────────────────────────────────────
        range_row = tk.Frame(ctrl_card, bg=BG2)
        range_row.pack(fill="x", padx=14, pady=(0, 2))

        _label(range_row, "上架时间选择：", size=9, fg=FG2, bg=BG2).pack(
            side="left", padx=(0, 6))

        _LISTING_PRESETS = ["最近7天", "最近30天", "最近90天", "今年", "全部"]
        self._listing_btns: dict[str, tk.Button] = {}
        saved_preset = self.settings.get("listing_preset", "最近30天")

        for label in _LISTING_PRESETS:
            b = tk.Button(
                range_row, text=label,
                font=("微软雅黑", 9), relief="flat", cursor="hand2",
                padx=10, pady=4, bd=0,
                bg=ACCENT if label == saved_preset else BG3,
                fg="white" if label == saved_preset else FG,
                activebackground=ACCENT2, activeforeground="white",
                command=lambda l=label: self._set_listing_preset(l),
            )
            b.pack(side="left", padx=(0, 4))
            self._listing_btns[label] = b

        # 自定义日期范围
        _label(range_row, "  自定义：", size=9, fg=FG2, bg=BG2).pack(side="left")
        self.listing_start_var = tk.StringVar(
            value=self.settings.get("listing_date_start", ""))
        self.listing_end_var = tk.StringVar(
            value=self.settings.get("listing_date_end", ""))
        s_e = _entry(range_row, self.listing_start_var, font=("Consolas", 9))
        s_e.config(width=11); s_e.pack(side="left", ipady=3, padx=(2, 0))
        _label(range_row, " ~ ", size=9, fg=FG2, bg=BG2).pack(side="left")
        e_e = _entry(range_row, self.listing_end_var, font=("Consolas", 9))
        e_e.config(width=11); e_e.pack(side="left", ipady=3)
        _label(range_row, " (YYYY-MM-DD)", size=8, fg=FG2, bg=BG2).pack(side="left")
        _btn(range_row, "确定", self._apply_listing_custom, bg=BG3).pack(
            side="left", padx=(4, 0))

        # 提示行
        hint_row = tk.Frame(ctrl_card, bg=BG2)
        hint_row.pack(fill="x", padx=14, pady=(0, 4))
        _label(hint_row,
               "※ 按产品上架日期（Date First Available）筛选新品，建议同时开启下方[采集上架日期]",
               size=8, fg=FG2, bg=BG2).pack(side="left")

        # ── 代理设置 ──────────────────────────────────────────────────────────
        proxy_row = tk.Frame(ctrl_card, bg=BG2)
        proxy_row.pack(fill="x", padx=14, pady=(0, 6))
        _label(proxy_row, "代理设置：", size=9, fg=FG2, bg=BG2).pack(side="left")
        self.proxy_var = tk.StringVar(value=self.settings.get("proxy", ""))
        proxy_entry = _entry(proxy_row, self.proxy_var, font=("Consolas", 9))
        proxy_entry.config(width=28)
        proxy_entry.pack(side="left", ipady=3, padx=(0, 4))
        _label(proxy_row, "例：http://127.0.0.1:7897", size=8, fg=FG2, bg=BG2).pack(side="left")
        _btn(proxy_row, "保存", self._save_proxy, bg=BG3).pack(side="left", padx=(6, 0))
        _btn(proxy_row, "测试连接", self._test_proxy, bg=BG3).pack(side="left", padx=(4, 0))
        self.proxy_status = _label(proxy_row, "", size=8, fg=GREEN, bg=BG2)
        self.proxy_status.pack(side="left", padx=(6, 0))

        # ── 行2：定时时间 + 操作按钮 ─────────────────────────────────────────
        row = tk.Frame(ctrl_card, bg=BG2)
        row.pack(fill="x", padx=14, pady=(0, 12))

        # 定时时间选择
        tbox = tk.Frame(row, bg=BG2)
        tbox.pack(side="left")
        _label(tbox, "每日定时运行（24小时制）", size=9, fg=FG2, bg=BG2).pack(anchor="w")
        tinner = tk.Frame(tbox, bg=BG3,
                          highlightthickness=1, highlightbackground=BORDER)
        tinner.pack(anchor="w", pady=4)
        h, m = self.settings["schedule_time"].split(":")
        self.hour_var = tk.StringVar(value=h)
        self.min_var  = tk.StringVar(value=m)
        _spin(tinner, self.hour_var, 0, 23, self._on_time_change).pack(
            side="left", padx=(8, 0), pady=4)
        tk.Label(tinner, text=":", font=("Consolas", 13, "bold"),
                 bg=BG3, fg=FG).pack(side="left")
        _spin(tinner, self.min_var, 0, 59, self._on_time_change).pack(
            side="left", padx=(0, 8), pady=4)

        # 采集上架日期开关
        self.fetch_date_var = tk.BooleanVar(
            value=self.settings.get("fetch_listing_date", True))
        tk.Checkbutton(
            tbox,
            text="✅ 采集上架日期+月销量（访问详情页，数据更准确）",
            variable=self.fetch_date_var,
            bg=BG2, fg=YELLOW, selectcolor=BG3,
            activebackground=BG2, font=("微软雅黑", 8, "bold"),
            command=self._on_fetch_date_toggle,
        ).pack(anchor="w", pady=(4, 0))

        # 操作按钮
        btns = tk.Frame(row, bg=BG2)
        btns.pack(side="right")
        _btn(btns, "▶  立即采集", self._run_now, bg=GREEN, fg="#1e1e2e").pack(
            side="left", padx=(0, 8))
        self.sched_btn = _btn(btns, "⏰  开始定时", self._toggle_schedule)
        self.sched_btn.pack(side="left", padx=(0, 8))
        _btn(btns, "📊  查看数据库", self._open_db_viewer, bg=BG3).pack(
            side="left", padx=(0, 8))
        _btn(btns, "📂  打开数据文件夹", self._open_data_folder, bg=BG3).pack(
            side="left")

        # 日志区
        log_card = tk.Frame(parent, bg=BG2,
                            highlightthickness=1, highlightbackground=BORDER)
        log_card.pack(fill="both", expand=True)

        lh = tk.Frame(log_card, bg=BG2)
        lh.pack(fill="x", padx=10, pady=(10, 0))
        _label(lh, "  运行日志", size=10, bold=True, bg=BG2).pack(side="left")
        _btn(lh, "清空", self._clear_log, bg=BG3).pack(side="right")

        self.log_text = scrolledtext.ScrolledText(
            log_card, font=("Consolas", 9),
            bg=BG3, fg=FG, relief="flat", bd=0,
            state="disabled", wrap="word", padx=10, pady=8)
        self.log_text.pack(fill="both", expand=True, padx=10, pady=8)
        self.log_text.tag_config("error",   foreground=RED)
        self.log_text.tag_config("warn",    foreground=YELLOW)
        self.log_text.tag_config("success", foreground=GREEN)
        self.log_text.tag_config("info",    foreground=FG)

    # ── 店铺管理 ──────────────────────────────────────────────────────────────

    def _refresh_seller_list(self):
        self.seller_lb.delete(0, "end")
        for s in self.settings["sellers"]:
            self.seller_lb.insert("end", f"  {s['name']}  ({s['id']})")

    def _add_seller(self):
        dlg = AddSellerDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            if any(s["id"] == dlg.result["id"] for s in self.settings["sellers"]):
                messagebox.showwarning("重复", f"卖家 ID {dlg.result['id']} 已存在。")
                return
            self.settings["sellers"].append(dlg.result)
            save_settings(self.settings)
            self._refresh_seller_list()
            logging.info("已添加店铺：%s (%s)", dlg.result["name"], dlg.result["id"])

    def _remove_seller(self):
        sel = self.seller_lb.curselection()
        if not sel:
            messagebox.showinfo("提示", "请先选中一个店铺。")
            return
        idx = sel[0]
        s = self.settings["sellers"][idx]
        if messagebox.askyesno("确认删除", f"确定要删除店铺「{s['name']}」？"):
            self.settings["sellers"].pop(idx)
            save_settings(self.settings)
            self._refresh_seller_list()
            logging.info("已删除店铺：%s", s["id"])

    # ── 上架时间选择 ──────────────────────────────────────────────────────────

    def _listing_date_range(self, preset: str):
        """Return (start_iso, end_iso) for the given preset label."""
        from datetime import date, timedelta
        today = date.today()
        mapping = {
            "最近7天":  (today - timedelta(days=6),  today),
            "最近30天": (today - timedelta(days=29), today),
            "最近90天": (today - timedelta(days=89), today),
            "今年":     (date(today.year, 1, 1),     today),
            "全部":     (date(2000, 1, 1),            today),
        }
        s, e = mapping.get(preset, (date(2000, 1, 1), today))
        return s.isoformat(), e.isoformat()

    def _set_listing_preset(self, label: str):
        start, end = self._listing_date_range(label)
        self.settings["listing_preset"]    = label
        self.settings["listing_date_start"] = start
        self.settings["listing_date_end"]   = end
        save_settings(self.settings)
        for lbl, btn in self._listing_btns.items():
            btn.config(bg=ACCENT if lbl == label else BG3,
                       fg="white" if lbl == label else FG)
        self.listing_start_var.set(start)
        self.listing_end_var.set(end)
        logging.info("上架时间筛选：%s  (%s ~ %s)", label, start, end)

    def _apply_listing_custom(self):
        start = self.listing_start_var.get().strip()
        end   = self.listing_end_var.get().strip()
        try:
            from datetime import datetime as _dt
            _dt.strptime(start, "%Y-%m-%d")
            _dt.strptime(end,   "%Y-%m-%d")
        except ValueError:
            messagebox.showwarning("输入错误", "请输入正确日期格式（YYYY-MM-DD）。")
            return
        if start > end:
            messagebox.showwarning("输入错误", "起始日期不能晚于结束日期。")
            return
        self.settings["listing_preset"]    = "自定义"
        self.settings["listing_date_start"] = start
        self.settings["listing_date_end"]   = end
        save_settings(self.settings)
        for btn in self._listing_btns.values():
            btn.config(bg=BG3, fg=FG)
        logging.info("上架时间筛选（自定义）：%s ~ %s", start, end)

    # ── 代理 ──────────────────────────────────────────────────────────────────

    def _save_proxy(self):
        proxy = self.proxy_var.get().strip()
        self.settings["proxy"] = proxy
        save_settings(self.settings)
        try:
            from amazon_scraper import config as _cfg
            _cfg.PROXY = proxy
        except Exception:
            pass
        if proxy:
            self.proxy_status.config(text="已保存 ✓", fg=GREEN)
            logging.info("代理已设置：%s", proxy)
        else:
            self.proxy_status.config(text="已清除", fg=FG2)
            logging.info("代理已清除")
        self.after(3000, lambda: self.proxy_status.config(text=""))

    def _test_proxy(self):
        proxy = self.proxy_var.get().strip()
        self.proxy_status.config(text="测试中...", fg=YELLOW)
        self.update_idletasks()

        def _do_test():
            import urllib.request
            test_url = "https://www.amazon.com/robots.txt"
            results = []

            def _try(label, proxies):
                try:
                    import requests as _req
                    r = _req.get(test_url, proxies=proxies,
                                 timeout=10, allow_redirects=True)
                    results.append((label, r.status_code, None))
                except Exception as e:
                    results.append((label, None, str(e)[:60]))

            if proxy:
                _try(f"HTTP代理({proxy})", {"http": proxy, "https": proxy})
                # Also try socks5
                socks_url = proxy.replace("http://", "socks5h://")
                if socks_url != proxy:
                    _try(f"SOCKS5({socks_url})", {"http": socks_url, "https": socks_url})
            else:
                _try("系统代理", None)

            self.after(0, lambda: self._show_test_result(results))

        threading.Thread(target=_do_test, daemon=True).start()

    def _show_test_result(self, results):
        success = [(l, c) for l, c, e in results if c and c < 400]
        errors  = [(l, e) for l, c, e in results if e]

        if success:
            label, code = success[0]
            self.proxy_status.config(text=f"连接成功 ✓ (HTTP {code})", fg=GREEN)
            logging.info("代理测试成功：%s → HTTP %s", label, code)
            # Auto-apply working proxy format
            for l, c, e in results:
                if c and c < 400 and "SOCKS5" in l:
                    socks_url = self.proxy_var.get().strip().replace("http://", "socks5h://")
                    self.proxy_var.set(socks_url)
                    self.settings["proxy"] = socks_url
                    save_settings(self.settings)
                    logging.info("已自动切换为 SOCKS5 代理：%s", socks_url)
                    break
        else:
            msgs = " | ".join(f"{l}: {e}" for l, e in errors[:2])
            self.proxy_status.config(text="连接失败 ✗", fg=RED)
            logging.error("代理测试失败：%s", msgs)
            messagebox.showerror("连接测试失败",
                f"无法通过代理连接 Amazon：\n\n{msgs}\n\n"
                "请检查：\n"
                "1. Clash/VPN 是否正在运行\n"
                "2. 是否有可用节点\n"
                "3. 端口号是否正确（Clash Verge 默认端口在设置→端口设置里查看）\n"
                "4. 尝试将 http:// 改为 socks5h://")

    # ── 采集 ──────────────────────────────────────────────────────────────────

    def _run_now(self):
        if self._scraper_thread and self._scraper_thread.is_alive():
            messagebox.showinfo("正在运行", "采集正在进行中，请稍候。")
            return
        ids = [s["id"] for s in self.settings["sellers"]]
        if not ids:
            messagebox.showwarning("无店铺", "请先添加至少一个店铺。")
            return
        self._scraper_thread = threading.Thread(
            target=self._scraper_worker, args=(ids,), daemon=True)
        self._scraper_thread.start()

    def _on_fetch_date_toggle(self):
        self.settings["fetch_listing_date"] = self.fetch_date_var.get()
        save_settings(self.settings)

    def _scraper_worker(self, seller_ids: list[str]):
        # Sync proxy setting to scraper config before each run
        try:
            from amazon_scraper import config as _cfg
            _cfg.PROXY = self.settings.get("proxy", "")
        except Exception:
            pass

        fetch_dates   = self.settings.get("fetch_listing_date", True)
        listing_start = self.settings.get("listing_date_start", "")
        listing_end   = self.settings.get("listing_date_end",   "")

        # If no date range stored yet, compute from preset
        if not listing_start or not listing_end:
            preset = self.settings.get("listing_preset", "最近30天")
            listing_start, listing_end = self._listing_date_range(preset)

        self._set_status("列表页采集中...", GREEN)

        def progress_cb(done: int, total: int):
            self._set_status(f"详情页 {done}/{total}...", YELLOW)

        try:
            from amazon_scraper.main import run_once
            run_once(seller_ids,
                     listing_date_start=listing_start,
                     listing_date_end=listing_end,
                     fetch_dates=fetch_dates,
                     progress_cb=progress_cb if fetch_dates else None)
        except ImportError as e:
            logging.error("依赖缺失：%s", e)
        except Exception as e:
            logging.exception("采集出错：%s", e)
        finally:
            self._set_status("待机", FG2)
            self.after(0, self._refresh_stats)

    def _set_status(self, text: str, color: str):
        self.after(0, lambda: self.status_dot.config(
            text=f"● {text}", fg=color))

    # ── 定时任务 ──────────────────────────────────────────────────────────────

    def _on_time_change(self):
        h = self.hour_var.get().zfill(2)
        m = self.min_var.get().zfill(2)
        self.settings["schedule_time"] = f"{h}:{m}"
        save_settings(self.settings)
        if self._scheduler_running:
            self._stop_schedule()
            self._start_schedule(silent=True)

    def _toggle_schedule(self):
        if self._scheduler_running:
            self._stop_schedule()
        else:
            self._start_schedule()

    def _start_schedule(self, silent=False):
        ids = [s["id"] for s in self.settings["sellers"]]
        if not ids and not silent:
            messagebox.showwarning("无店铺", "请先添加至少一个店铺。")
            return
        t = self.settings["schedule_time"]
        schedule_lib.clear()
        schedule_lib.every().day.at(t).do(
            lambda: self._scraper_worker(list(ids)))
        self._scheduler_running = True
        self.settings["schedule_enabled"] = True
        save_settings(self.settings)
        self.sched_btn.config(text="⏹  停止定时", bg=RED)
        self._set_status(f"已定时 {t}", YELLOW)
        logging.info("定时任务已启动，将于每天 %s 自动采集", t)
        if not (self._scheduler_thread and self._scheduler_thread.is_alive()):
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop, daemon=True)
            self._scheduler_thread.start()

    def _stop_schedule(self):
        self._scheduler_running = False
        self.settings["schedule_enabled"] = False
        save_settings(self.settings)
        schedule_lib.clear()
        self.sched_btn.config(text="⏰  开始定时", bg=BG3)
        self._set_status("待机", FG2)
        logging.info("定时任务已停止。")

    def _scheduler_loop(self):
        while self._scheduler_running:
            schedule_lib.run_pending()
            time.sleep(20)

    # ── 统计刷新 ──────────────────────────────────────────────────────────────

    def _refresh_stats(self):
        try:
            from amazon_scraper.db import get_stats, DB_PATH
            if not DB_PATH.exists():
                return
            s = get_stats()
            self.stat_vars["total"].set(str(s["total_products"]))
            self.stat_vars["new"].set(str(s["new_today"]))
            self.stat_vars["sellers"].set(str(s["sellers"]))
            self.stat_vars["last"].set(str(s["last_run"]))
        except Exception:
            pass

    # ── 工具按钮 ──────────────────────────────────────────────────────────────

    def _open_db_viewer(self):
        DBViewerDialog(self)

    def _open_data_folder(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(DATA_DIR))
        elif sys.platform == "darwin":
            os.system(f'open "{DATA_DIR}"')
        else:
            os.system(f'xdg-open "{DATA_DIR}"')

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _on_close(self):
        if self._scraper_thread and self._scraper_thread.is_alive():
            if not messagebox.askyesno("采集中", "采集正在进行，确定要退出吗？"):
                return
        self.destroy()


# ── 辅助控件 ──────────────────────────────────────────────────────────────────

def _spin(parent, var, frm, to, cmd):
    return tk.Spinbox(parent, from_=frm, to=to, width=3,
                      textvariable=var, format="%02.0f",
                      font=("Consolas", 13, "bold"),
                      bg=BG3, fg=FG, buttonbackground=BG3,
                      relief="flat", bd=0, insertbackground=FG,
                      command=cmd)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
