"""
Entry point: run one scrape cycle for all configured sellers.
Writes results to both CSV snapshots and the SQLite database.
"""
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from . import config
from .scraper import scrape_seller, fetch_listing_dates
from .storage import save_snapshot, find_new_products, append_new_products
from .db import init_db, upsert_products, log_run


def _setup_logging() -> None:
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    log_file = Path(config.LOG_DIR) / f"scraper_{date.today().isoformat()}.log"
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def run_once(seller_ids: list[str] | None = None,
             days_back: int | None = None,
             fetch_dates: bool = False) -> None:
    _setup_logging()
    logger = logging.getLogger(__name__)

    ids = seller_ids or config.SELLER_IDS
    if not ids:
        logger.error("未配置店铺ID，请在设置中添加店铺。")
        sys.exit(1)

    lookback = days_back if days_back is not None else config.NEW_PRODUCT_DAYS
    logger.info("新品判断范围：过去 %d 天", lookback)

    init_db()
    today = date.today()
    all_new: list[dict] = []

    for seller_id in ids:
        started_at = datetime.now()
        status = "success"
        try:
            products = scrape_seller(seller_id)
            save_snapshot(seller_id, products, today)

            new = find_new_products(seller_id, products, today,
                                    days_back=lookback)
            new_asins = {p["asin"] for p in new}
            all_new.extend(new)

            # Optionally enrich new products with listing date from detail pages
            if fetch_dates and new:
                logger.info("采集 %d 个新产品的上架日期...", len(new))
                fetch_listing_dates(new, max_workers=3)

            upsert_products(products, new_asins, today)
            log_run(seller_id, len(products), len(new), started_at)
        except Exception:
            status = "error"
            logger.exception("采集店铺 %s 时发生错误", seller_id)
            log_run(seller_id, 0, 0, started_at, status="error")

    if all_new:
        out_path = append_new_products(all_new, today)
        logger.info("完成。共发现 %d 个新产品，已保存至 %s", len(all_new), out_path)
    else:
        logger.info("完成。今日未发现新产品。")
