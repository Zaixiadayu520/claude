"""
Entry point: run one scrape cycle for all configured sellers.
"""
import logging
import os
import sys
from datetime import date
from pathlib import Path

from . import config
from .scraper import scrape_seller
from .storage import save_snapshot, find_new_products, append_new_products


def _setup_logging() -> None:
    log_dir = Path(config.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"scraper_{date.today().isoformat()}.log"

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def run_once(seller_ids: list[str] | None = None) -> None:
    """Run a full scrape-and-diff cycle. Uses config.SELLER_IDS by default."""
    _setup_logging()
    logger = logging.getLogger(__name__)

    ids = seller_ids or config.SELLER_IDS
    if not ids:
        logger.error(
            "No seller IDs configured. "
            "Add them to amazon_scraper/config.py → SELLER_IDS."
        )
        sys.exit(1)

    today = date.today()
    all_new: list[dict] = []

    for seller_id in ids:
        try:
            products = scrape_seller(seller_id)
            save_snapshot(seller_id, products, today)
            new = find_new_products(seller_id, products, today)
            all_new.extend(new)
        except Exception:
            logger.exception("Unhandled error while processing seller %s", seller_id)

    if all_new:
        out_path = append_new_products(all_new, today)
        logger.info("Done. %d new products written to %s", len(all_new), out_path)
    else:
        logger.info("Done. No new products found today.")
