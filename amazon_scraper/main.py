"""
Entry point: run one scrape cycle for all configured sellers.
Writes results to both CSV snapshots and the SQLite database.
"""
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from . import config
from .scraper import scrape_seller, batch_enrich_products
from .storage import save_snapshot, find_new_products, append_new_products
from .db import init_db, upsert_products, log_run, get_known_asins


def _setup_logging() -> None:
    Path(config.LOG_DIR).mkdir(parents=True, exist_ok=True)
    log_file = Path(config.LOG_DIR) / f"scraper_{date.today().isoformat()}.log"
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    # Reconfigure (may already be set up by GUI)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
        root.addHandler(h)


def run_once(seller_ids: list[str] | None = None,
             days_back: int | None = None,
             fetch_dates: bool = False,
             progress_cb=None) -> None:
    """
    Full scrape cycle.

    fetch_dates=True  → after listing pages are done, visit each product's
                        detail page to obtain date_first_available and
                        monthly_sales (for products that don't have them yet).
    progress_cb       → optional callable(done, total) for GUI progress bar.
    """
    logger = logging.getLogger(__name__)

    ids = seller_ids or config.SELLER_IDS
    if not ids:
        logger.error("未配置店铺ID，请在设置中添加店铺。")
        sys.exit(1)

    lookback = days_back if days_back is not None else config.NEW_PRODUCT_DAYS
    logger.info("新品判断范围：过去 %d 天", lookback)
    if fetch_dates:
        logger.info("已开启采集上架日期（将访问详情页）")

    init_db()
    today    = date.today()
    all_new: list[dict] = []
    # Collect all products across sellers so we do one batched detail fetch
    all_products_for_enrich: list[dict] = []

    for seller_id in ids:
        started_at = datetime.now()
        try:
            products = scrape_seller(seller_id)
            save_snapshot(seller_id, products, today)

            new      = find_new_products(seller_id, products, today, days_back=lookback)
            new_asins = {p["asin"] for p in new}
            all_new.extend(new)

            if fetch_dates:
                all_products_for_enrich.extend(products)
            else:
                # Still upsert what we have from listing pages
                upsert_products(products, new_asins, today)

            log_run(seller_id, len(products), len(new), started_at)
        except Exception:
            logger.exception("采集店铺 %s 时发生错误", seller_id)
            log_run(seller_id, 0, 0, started_at, status="error")

    # ── Detail page batch enrichment ─────────────────────────────────────────
    if fetch_dates and all_products_for_enrich:
        logger.info("开始批量采集详情页（%d 个产品）...", len(all_products_for_enrich))
        batch_enrich_products(all_products_for_enrich,
                              max_workers=config.CONCURRENT_PAGES,
                              progress_cb=progress_cb)
        # Now upsert enriched products (one seller at a time)
        from itertools import groupby
        keyfn = lambda p: p.get("seller_id", "")
        for sid, grp in groupby(sorted(all_products_for_enrich, key=keyfn), keyfn):
            prods    = list(grp)
            new_asns = {p["asin"] for p in all_new if p.get("seller_id") == sid}
            upsert_products(prods, new_asns, today)

    if all_new:
        out_path = append_new_products(all_new, today)
        logger.info("完成。共发现 %d 个新产品，已保存至 %s", len(all_new), out_path)
    else:
        logger.info("完成。今日未发现新产品。")
