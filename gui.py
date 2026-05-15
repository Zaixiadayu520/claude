"""
Amazon New Product Scraper - GUI Application
"""
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import json
import os
import sys
import queue
import logging
import schedule as schedule_lib
import time
from pathlib import Path
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
SETTINGS_FILE = BASE_DIR / "settings.json"
DATA_DIR = BASE_DIR / "data"

# ── Default settings ──────────────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "sellers": [],
    "schedule_time": "08:00",
    "schedule_enabled": False,
}

# ── Colors ────────────────────────────────────────────────────────────────────
BG        = "#1e1e2e"
BG2       = "#2a2a3e"
BG3       = "#313145"
ACCENT    = "#7c6af7"
ACCENT2   = "#5a4fcf"
GREEN     = "#50fa7b"
RED       = "#ff5555"
YELLOW    = "#f1fa8c"
FG        = "#cdd6f4"
FG2       = "#a6adc8"
BORDER    = "#45475a"


# ── Settings persistence ──────────────────────────────────────────────────────

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            # Merge with defaults to handle missing keys
            return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


# ── Queue-based log handler ───────────────────────────────────────────────────

class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))


# ── Add Seller Dialog ─────────────────────────────────────────────────────────

class AddSellerDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.result = None
        self.title("Add Seller")
        self.resizable(False, False)
        self.configure(bg=BG2)
        self.grab_set()

        # Center on parent
        self.geometry(f"400x220+{parent.winfo_rootx()+150}+{parent.winfo_rooty()+150}")

        tk.Label(self, text="Add New Seller", font=("Segoe UI", 13, "bold"),
                 bg=BG2, fg=FG).pack(pady=(20, 10))

        form = tk.Frame(self, bg=BG2)
        form.pack(padx=30, fill="x")

        tk.Label(form, text="Seller ID *", font=("Segoe UI", 9),
                 bg=BG2, fg=FG2).grid(row=0, column=0, sticky="w", pady=4)
        self.id_var = tk.StringVar()
        id_entry = tk.Entry(form, textvariable=self.id_var, font=("Consolas", 10),
                            bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                            highlightthickness=1, highlightcolor=ACCENT,
                            highlightbackground=BORDER)
        id_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=4, ipady=4)
        id_entry.focus()

        tk.Label(form, text="Nickname", font=("Segoe UI", 9),
                 bg=BG2, fg=FG2).grid(row=1, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar()
        name_entry = tk.Entry(form, textvariable=self.name_var, font=("Segoe UI", 10),
                              bg=BG3, fg=FG, insertbackground=FG, relief="flat",
                              highlightthickness=1, highlightcolor=ACCENT,
                              highlightbackground=BORDER)
        name_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=4, ipady=4)

        form.columnconfigure(1, weight=1)

        self.err_label = tk.Label(self, text="", font=("Segoe UI", 8),
                                  bg=BG2, fg=RED)
        self.err_label.pack()

        btn_frame = tk.Frame(self, bg=BG2)
        btn_frame.pack(pady=10)

        _btn(btn_frame, "Cancel", self.destroy, bg=BG3).pack(side="left", padx=6)
        _btn(btn_frame, "Add Seller", self._confirm, bg=ACCENT).pack(side="left", padx=6)

        self.bind("<Return>", lambda e: self._confirm())
        self.bind("<Escape>", lambda e: self.destroy())

    def _confirm(self):
        sid = self.id_var.get().strip()
        name = self.name_var.get().strip() or sid
        if not sid:
            self.err_label.config(text="Seller ID is required.")
            return
        self.result = {"id": sid, "name": name}
        self.destroy()


# ── Helper widgets ────────────────────────────────────────────────────────────

def _btn(parent, text, command, bg=BG3, fg=FG, width=None):
    kw = dict(text=text, command=command, bg=bg, fg=fg,
              font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2",
              padx=14, pady=6, activebackground=ACCENT2, activeforeground="white",
              bd=0)
    if width:
        kw["width"] = width
    return tk.Button(parent, **kw)


def _label(parent, text, size=9, bold=False, fg=FG, bg=None):
    font = ("Segoe UI", size, "bold" if bold else "normal")
    return tk.Label(parent, text=text, font=font, fg=fg,
                    bg=bg or parent["bg"])


# ── Main Application ──────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Amazon New Product Scraper")
        self.geometry("900x640")
        self.minsize(800, 560)
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

        # Restore schedule state
        if self.settings.get("schedule_enabled"):
            self._start_schedule(silent=True)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Logging ───────────────────────────────────────────────────────────────

    def _setup_logging(self):
        self._log_handler = QueueHandler(self.log_queue)
        self._log_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                              datefmt="%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self._log_handler)

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
        elif "Done" in msg or "new products" in msg.lower():
            tag = "success"
        else:
            tag = "info"
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ── UI Build ──────────────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────────
        header = tk.Frame(self, bg=BG2, height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="🛒  Amazon New Product Scraper",
                 font=("Segoe UI", 14, "bold"), bg=BG2, fg=FG).pack(
            side="left", padx=20, pady=12)
        self.status_dot = tk.Label(header, text="●  Idle",
                                   font=("Segoe UI", 9), bg=BG2, fg=FG2)
        self.status_dot.pack(side="right", padx=20)

        # ── Main body ─────────────────────────────────────────────────────────
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=12, pady=(10, 6))

        # Left panel (sellers)
        left = tk.Frame(body, bg=BG2, bd=0, relief="flat",
                        highlightthickness=1, highlightbackground=BORDER)
        left.pack(side="left", fill="y", padx=(0, 8))
        left.pack_propagate(False)
        left.configure(width=260)
        self._build_seller_panel(left)

        # Right panel (settings + log)
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        self._build_right_panel(right)

    def _build_seller_panel(self, parent):
        _label(parent, "  Seller Stores", size=10, bold=True, bg=BG2).pack(
            anchor="w", pady=(14, 6))

        # Seller listbox
        list_frame = tk.Frame(parent, bg=BG2)
        list_frame.pack(fill="both", expand=True, padx=10)

        scrollbar = tk.Scrollbar(list_frame, bg=BG3, troughcolor=BG2,
                                 relief="flat", bd=0)
        scrollbar.pack(side="right", fill="y")

        self.seller_listbox = tk.Listbox(
            list_frame,
            yscrollcommand=scrollbar.set,
            bg=BG3, fg=FG,
            selectbackground=ACCENT, selectforeground="white",
            font=("Segoe UI", 10),
            relief="flat", bd=0,
            highlightthickness=0,
            activestyle="none",
        )
        self.seller_listbox.pack(fill="both", expand=True)
        scrollbar.config(command=self.seller_listbox.yview)

        # Buttons
        btn_frame = tk.Frame(parent, bg=BG2)
        btn_frame.pack(fill="x", padx=10, pady=10)

        _btn(btn_frame, "+ Add Store", self._add_seller,
             bg=ACCENT).pack(fill="x", pady=(0, 4))
        _btn(btn_frame, "✕ Remove Selected", self._remove_seller,
             bg=BG3).pack(fill="x")

    def _build_right_panel(self, parent):
        # ── Settings card ─────────────────────────────────────────────────────
        card = tk.Frame(parent, bg=BG2,
                        highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="x", pady=(0, 8))

        _label(card, "  Schedule & Control", size=10, bold=True, bg=BG2).pack(
            anchor="w", pady=(12, 8), padx=4)

        row1 = tk.Frame(card, bg=BG2)
        row1.pack(fill="x", padx=14, pady=(0, 10))

        # Time picker
        time_box = tk.Frame(row1, bg=BG2)
        time_box.pack(side="left")
        _label(time_box, "Run Time (24h)", size=9, fg=FG2, bg=BG2).pack(anchor="w")
        time_inner = tk.Frame(time_box, bg=BG3,
                              highlightthickness=1, highlightbackground=BORDER)
        time_inner.pack(anchor="w", pady=4)

        h, m = self.settings["schedule_time"].split(":")
        self.hour_var = tk.StringVar(value=h)
        self.min_var = tk.StringVar(value=m)

        hour_spin = tk.Spinbox(time_inner, from_=0, to=23, width=3,
                               textvariable=self.hour_var, format="%02.0f",
                               font=("Consolas", 13, "bold"),
                               bg=BG3, fg=FG, buttonbackground=BG3,
                               relief="flat", bd=0, insertbackground=FG,
                               command=self._on_time_change)
        hour_spin.pack(side="left", padx=(8, 0), pady=4)
        tk.Label(time_inner, text=":", font=("Consolas", 13, "bold"),
                 bg=BG3, fg=FG).pack(side="left")
        min_spin = tk.Spinbox(time_inner, from_=0, to=59, width=3,
                              textvariable=self.min_var, format="%02.0f",
                              font=("Consolas", 13, "bold"),
                              bg=BG3, fg=FG, buttonbackground=BG3,
                              relief="flat", bd=0, insertbackground=FG,
                              command=self._on_time_change)
        min_spin.pack(side="left", padx=(0, 8), pady=4)

        # Buttons
        btns = tk.Frame(row1, bg=BG2)
        btns.pack(side="right")

        _btn(btns, "▶  Run Now", self._run_now, bg=GREEN,
             fg="#1e1e2e").pack(side="left", padx=(0, 8))
        self.schedule_btn = _btn(btns, "⏰  Start Schedule",
                                 self._toggle_schedule, bg=BG3)
        self.schedule_btn.pack(side="left")

        _btn(btns, "📂  Open Data Folder",
             self._open_data_folder, bg=BG3).pack(side="left", padx=(8, 0))

        # ── Log area ──────────────────────────────────────────────────────────
        log_card = tk.Frame(parent, bg=BG2,
                            highlightthickness=1, highlightbackground=BORDER)
        log_card.pack(fill="both", expand=True)

        log_header = tk.Frame(log_card, bg=BG2)
        log_header.pack(fill="x", padx=10, pady=(10, 0))
        _label(log_header, "  Run Log", size=10, bold=True, bg=BG2).pack(side="left")
        _btn(log_header, "Clear", self._clear_log, bg=BG3).pack(side="right")

        self.log_text = scrolledtext.ScrolledText(
            log_card,
            font=("Consolas", 9),
            bg=BG3, fg=FG,
            relief="flat", bd=0,
            state="disabled",
            wrap="word",
            padx=10, pady=8,
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=8)
        self.log_text.tag_config("error",   foreground=RED)
        self.log_text.tag_config("warn",    foreground=YELLOW)
        self.log_text.tag_config("success", foreground=GREEN)
        self.log_text.tag_config("info",    foreground=FG)

    # ── Seller management ─────────────────────────────────────────────────────

    def _refresh_seller_list(self):
        self.seller_listbox.delete(0, "end")
        for s in self.settings["sellers"]:
            label = f"  {s['name']}  ({s['id']})"
            self.seller_listbox.insert("end", label)

    def _add_seller(self):
        dlg = AddSellerDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            # Check duplicate
            existing_ids = [s["id"] for s in self.settings["sellers"]]
            if dlg.result["id"] in existing_ids:
                messagebox.showwarning("Duplicate",
                    f"Seller ID {dlg.result['id']} is already in the list.")
                return
            self.settings["sellers"].append(dlg.result)
            save_settings(self.settings)
            self._refresh_seller_list()
            logging.info("Added seller: %s (%s)",
                         dlg.result["name"], dlg.result["id"])

    def _remove_seller(self):
        sel = self.seller_listbox.curselection()
        if not sel:
            messagebox.showinfo("Select a store",
                                "Please select a store to remove.")
            return
        idx = sel[0]
        seller = self.settings["sellers"][idx]
        if messagebox.askyesno("Confirm",
                f"Remove store '{seller['name']}' ({seller['id']})?"):
            self.settings["sellers"].pop(idx)
            save_settings(self.settings)
            self._refresh_seller_list()
            logging.info("Removed seller: %s", seller["id"])

    # ── Run scraper ───────────────────────────────────────────────────────────

    def _run_now(self):
        if self._scraper_thread and self._scraper_thread.is_alive():
            messagebox.showinfo("Running",
                "Scraper is already running. Please wait.")
            return
        seller_ids = [s["id"] for s in self.settings["sellers"]]
        if not seller_ids:
            messagebox.showwarning("No Sellers",
                "Please add at least one seller store first.")
            return
        self._scraper_thread = threading.Thread(
            target=self._scraper_worker, args=(seller_ids,), daemon=True)
        self._scraper_thread.start()

    def _scraper_worker(self, seller_ids: list[str]):
        self._set_status("Running...", GREEN)
        try:
            # Import here so GUI loads fast even if deps missing
            from amazon_scraper.main import run_once
            run_once(seller_ids)
        except ImportError as e:
            logging.error("Import error: %s — make sure dependencies are installed.", e)
        except Exception as e:
            logging.exception("Scraper error: %s", e)
        finally:
            self._set_status("Idle", FG2)

    def _set_status(self, text: str, color: str):
        self.after(0, lambda: self.status_dot.config(
            text=f"●  {text}", fg=color))

    # ── Schedule ──────────────────────────────────────────────────────────────

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
        seller_ids = [s["id"] for s in self.settings["sellers"]]
        if not seller_ids and not silent:
            messagebox.showwarning("No Sellers",
                "Please add at least one seller store first.")
            return

        t = self.settings["schedule_time"]
        schedule_lib.clear()
        schedule_lib.every().day.at(t).do(
            lambda: self._scraper_worker(seller_ids))

        self._scheduler_running = True
        self.settings["schedule_enabled"] = True
        save_settings(self.settings)

        self.schedule_btn.config(text="⏹  Stop Schedule", bg=RED)
        self._set_status(f"Scheduled at {t}", YELLOW)
        logging.info("Schedule started — will run daily at %s", t)

        if self._scheduler_thread is None or not self._scheduler_thread.is_alive():
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop, daemon=True)
            self._scheduler_thread.start()

    def _stop_schedule(self):
        self._scheduler_running = False
        self.settings["schedule_enabled"] = False
        save_settings(self.settings)
        schedule_lib.clear()
        self.schedule_btn.config(text="⏰  Start Schedule", bg=BG3)
        self._set_status("Idle", FG2)
        logging.info("Schedule stopped.")

    def _scheduler_loop(self):
        while self._scheduler_running:
            schedule_lib.run_pending()
            time.sleep(20)

    # ── Misc ──────────────────────────────────────────────────────────────────

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
            if not messagebox.askyesno("Scraper Running",
                    "Scraper is still running. Quit anyway?"):
                return
        self.destroy()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
