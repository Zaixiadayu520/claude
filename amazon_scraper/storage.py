"""
CSV-based storage: daily snapshots + new-product diff.
"""
import csv
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import config

logger = logging.getLogger(__name__)


def safe_seller_id(seller_id: str) -> str:
    """If seller_id is a full URL, extract the 'me=' parameter value.
    Then strip any characters that are illegal in Windows filenames."""
    import re
    m = re.search(r'[?&]me=([A-Z0-9]+)', seller_id, re.IGNORECASE)
    if m:
        return m.group(1)
    return re.sub(r'[\\/:*?"<>|\s]', '_', seller_id)


# Canonical column order for all CSV files
COLUMNS = [
    "seller_id", "asin", "title", "brand",
    "price", "list_price", "rating", "review_count",
    "prime", "sponsored", "main_image_url", "url",
]


def _data_dir() -> Path:
    p = Path(config.DATA_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def snapshot_path(seller_id: str, for_date: Optional[date] = None) -> Path:
    d = for_date or date.today()
    return _data_dir() / f"snapshot_{safe_seller_id(seller_id)}_{d.isoformat()}.csv"


def new_products_path(for_date: Optional[date] = None) -> Path:
    d = for_date or date.today()
    return _data_dir() / f"new_products_{d.isoformat()}.csv"


def save_snapshot(seller_id: str, products: list[dict],
                  for_date: Optional[date] = None) -> Path:
    """Write today's full product list for a seller to a snapshot CSV."""
    path = snapshot_path(seller_id, for_date)
    _write_csv(path, products)
    logger.info("Snapshot saved: %s (%d rows)", path, len(products))
    return path


def load_snapshot(seller_id: str, for_date: Optional[date] = None) -> list[dict]:
    """Load a snapshot CSV; return [] if file not found."""
    path = snapshot_path(seller_id, for_date)
    if not path.exists():
        return []
    return _read_csv(path)


def find_new_products(seller_id: str,
                      today_products: list[dict],
                      today: Optional[date] = None,
                      days_back: int = 1) -> list[dict]:
    """
    Return products whose ASIN was NOT seen in the past `days_back` days.

    Lookup order:
      1. SQLite database (fast, covers all past runs)
      2. CSV snapshots  (fallback when DB is unavailable)
    """
    today = today or date.today()
    known_asins: set[str] = set()

    # 1. Try database
    try:
        from .db import get_known_asins
        known_asins = get_known_asins(seller_id, days_back, today)
    except Exception as exc:
        logger.debug("DB lookup failed (%s), falling back to CSV snapshots", exc)

    # 2. CSV fallback
    if not known_asins:
        for offset in range(1, days_back + 1):
            snap = load_snapshot(seller_id, today - timedelta(days=offset))
            known_asins |= {r["asin"] for r in snap if r.get("asin")}

    if not known_asins:
        logger.info(
            "无历史数据（%s），所有 %d 个产品视为新品",
            seller_id, len(today_products),
        )

    new = [p for p in today_products if p.get("asin") not in known_asins]
    logger.info("店铺 %s：发现 %d 个新产品（对比过去 %d 天）",
                seller_id, len(new), days_back)
    return new


def append_new_products(new_products: list[dict],
                        for_date: Optional[date] = None) -> Path:
    """Append new products to today's combined new-products CSV."""
    path = new_products_path(for_date)
    file_exists = path.exists()
    _write_csv(path, new_products, append=file_exists)
    logger.info("Appended %d new products → %s", len(new_products), path)
    return path


# ── CSV helpers ───────────────────────────────────────────────────────────────

def _write_csv(path: Path, rows: list[dict], append: bool = False) -> None:
    mode = "a" if append else "w"
    with open(path, mode, newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if not append:
            writer.writeheader()
        for row in rows:
            writer.writerow({col: row.get(col, "") for col in COLUMNS})


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))
