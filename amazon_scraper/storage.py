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
    return _data_dir() / f"snapshot_{seller_id}_{d.isoformat()}.csv"


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
                      today: Optional[date] = None) -> list[dict]:
    """
    Compare today's products against yesterday's snapshot.
    Return products whose ASIN was not present yesterday.
    """
    today = today or date.today()
    yesterday = today - timedelta(days=1)

    yesterday_snapshot = load_snapshot(seller_id, yesterday)
    yesterday_asins = {row["asin"] for row in yesterday_snapshot if row.get("asin")}

    if not yesterday_asins:
        logger.info(
            "No yesterday snapshot for seller %s — all %d products treated as new",
            seller_id, len(today_products),
        )

    new = [p for p in today_products if p.get("asin") not in yesterday_asins]
    logger.info("Seller %s: %d new products (vs yesterday)", seller_id, len(new))
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
