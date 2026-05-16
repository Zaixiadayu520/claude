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
from .storage import save_snapshot, append_new_products
from .db import init_db, upsert_products, log_run, get_existing_product_data


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


def _filter_by_listing_date(products: list[dict],
                            start: str, end: str) -> set[str]:
    """Return ASINs whose date_first_available falls within [start, end]."""
    matched: set[str] = set()
    for p in products:
        dfa = (p.get("date_first_available") or "").strip()
        if dfa and start <= dfa <= end:
            matched.add(p["asin"])
    return matched


def run_once(seller_ids: list[str] | None = None,
             listing_date_start: str | None = None,
             listing_date_end:   str | None = None,
             fetch_dates: bool = True,
             progress_cb=None) -> None:
    """
    Full scrape cycle.

    listing_date_start/end -> ISO date strings (YYYY-MM-DD).  Products whose
                             date_first_available falls in this range are
                             flagged as new.  Defaults to past 30 days.
    fetch_dates=True       -> visit each product's detail page to collect
                             date_first_available and monthly_sales.
    progress_cb            -> optional callable(done, total) for GUI progress.
    """
    logger = logging.getLogger(__name__)

    ids = seller_ids or config.SELLER_IDS
    if not ids:
        logger.error("未配置店铺ID，请在设置中添加店铺。")
        sys.exit(1)

    # Resolve date range
    if not listing_date_start or not listing_date_end:
        from datetime import timedelta
        listing_date_end   = date.today().isoformat()
        listing_date_start = (date.today() - timedelta(days=29)).isoformat()

    logger.info("上架时间筛选范围：%s ~ %s", listing_date_start, listing_date_end)
    if fetch_dates:
        logger.info("已开启采集上架日期（将访问详情页）")

    init_db()
    today = date.today()
    all_products_for_enrich: list[dict] = []
    all_sellers_products: list[tuple[str, list[dict]]] = []

    for seller_id in ids:
        started_at = datetime.now()
        try:
            products = scrape_seller(seller_id)
            save_snapshot(seller_id, products, today)
            all_sellers_products.append((seller_id, products))
            if fetch_dates:
                all_products_for_enrich.extend(products)
            else:
                # Upsert with empty new_asins for now; update after date filter
                upsert_products(products, set(), today)
            log_run(seller_id, len(products), 0, started_at)
        except Exception:
            logger.exception("采集店铺 %s 时发生错误", seller_id)
            log_run(seller_id, 0, 0, started_at, status="error")

    # ── Detail page batch enrichment ─────────────────────────────────────────
    if fetch_dates and all_products_for_enrich:
        # Pre-populate from DB — only visit detail pages for truly new/unknown products
        asins = [p["asin"] for p in all_products_for_enrich]
        cached = get_existing_product_data(asins)
        prefilled = 0
        for p in all_products_for_enrich:
            cached_data = cached.get(p["asin"])
            if cached_data:
                if not p.get("date_first_available"):
                    p["date_first_available"] = cached_data["date_first_available"]
                if not p.get("monthly_sales") and cached_data.get("monthly_sales"):
                    p["monthly_sales"] = cached_data["monthly_sales"]
                prefilled += 1
        need_visit = sum(1 for p in all_products_for_enrich if not p.get("date_first_available"))
        logger.info("DB缓存命中 %d 个产品，还需访问 %d 个详情页", prefilled, need_visit)
        logger.info("开始批量采集详情页（%d 个产品）...", need_visit)
        batch_enrich_products(all_products_for_enrich,
                              max_workers=getattr(config, "CONCURRENT_DETAIL_PAGES", 2),
                              progress_cb=progress_cb)

    # ── Apply listing date filter and upsert ──────────────────────────────────
    all_new: list[dict] = []
    for seller_id, products in all_sellers_products:
        new_asins = _filter_by_listing_date(
            products, listing_date_start, listing_date_end)
        new = [p for p in products if p["asin"] in new_asins]
        all_new.extend(new)
        logger.info("店铺 %s：发现 %d 个新上架产品（%s ~ %s）",
                    seller_id, len(new), listing_date_start, listing_date_end)
        upsert_products(products, new_asins, today)
        # Update run record with correct new count
        log_run(seller_id, len(products), len(new), datetime.now())

    if all_new:
        out_path = append_new_products(all_new, today)
        logger.info("完成。共发现 %d 个新上架产品，已保存至 %s", len(all_new), out_path)
    else:
        logger.info("完成。所选上架时间范围内未发现新产品。")
