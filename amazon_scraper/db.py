"""
SQLite database layer.

Schema
------
products    : one row per (asin, scraped_date); upsert on re-run
scrape_runs : one row per (seller_id, run_date) recording totals
"""
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

DB_PATH = Path(config.DATA_DIR) / "amazon_scraper.db"

DDL = """
CREATE TABLE IF NOT EXISTS products (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id            TEXT    NOT NULL,
    asin                 TEXT    NOT NULL,
    title                TEXT,
    brand                TEXT,
    price                TEXT,
    list_price           TEXT,
    rating               TEXT,
    review_count         TEXT,
    prime                TEXT,
    sponsored            TEXT,
    monthly_sales        TEXT    NOT NULL DEFAULT '',
    date_first_available TEXT    NOT NULL DEFAULT '',
    main_image_url       TEXT,
    url                  TEXT,
    scraped_date         TEXT    NOT NULL,
    is_new               INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (asin, scraped_date)
);

CREATE INDEX IF NOT EXISTS idx_products_seller  ON products(seller_id);
CREATE INDEX IF NOT EXISTS idx_products_date    ON products(scraped_date);
CREATE INDEX IF NOT EXISTS idx_products_is_new  ON products(is_new);
CREATE INDEX IF NOT EXISTS idx_products_dfa     ON products(date_first_available);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id      TEXT    NOT NULL,
    run_date       TEXT    NOT NULL,
    total_products INTEGER NOT NULL DEFAULT 0,
    new_products   INTEGER NOT NULL DEFAULT 0,
    status         TEXT    NOT NULL DEFAULT 'success',
    started_at     TEXT,
    finished_at    TEXT,
    UNIQUE (seller_id, run_date)
);
"""

# Columns added after initial release — applied as migrations on existing DBs
_NEW_COLUMNS = [
    ("monthly_sales",        "TEXT NOT NULL DEFAULT ''"),
    ("date_first_available", "TEXT NOT NULL DEFAULT ''"),
]


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript(DDL)
        # Migrate existing databases that predate new columns
        existing = {row[1] for row in con.execute("PRAGMA table_info(products)")}
        for col_name, col_def in _NEW_COLUMNS:
            if col_name not in existing:
                con.execute(
                    f"ALTER TABLE products ADD COLUMN {col_name} {col_def}")
                logger.info("DB migration: added column %s", col_name)
    logger.info("Database ready: %s", DB_PATH)


def upsert_products(products: list[dict],
                    new_asins: set[str],
                    scraped_date: date | None = None) -> None:
    """Insert or replace products; mark rows whose ASIN is in new_asins."""
    day = (scraped_date or date.today()).isoformat()
    rows = [
        (
            p.get("seller_id", ""),
            p.get("asin", ""),
            p.get("title", ""),
            p.get("brand", ""),
            p.get("price", ""),
            p.get("list_price", ""),
            p.get("rating", ""),
            p.get("review_count", ""),
            p.get("prime", ""),
            p.get("sponsored", ""),
            p.get("monthly_sales", ""),
            p.get("date_first_available", ""),
            p.get("main_image_url", ""),
            p.get("url", ""),
            day,
            1 if p.get("asin") in new_asins else 0,
        )
        for p in products
        if p.get("asin")
    ]
    sql = """
        INSERT INTO products
            (seller_id, asin, title, brand, price, list_price,
             rating, review_count, prime, sponsored,
             monthly_sales, date_first_available,
             main_image_url, url, scraped_date, is_new)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(asin, scraped_date) DO UPDATE SET
            title                = excluded.title,
            brand                = excluded.brand,
            price                = excluded.price,
            list_price           = excluded.list_price,
            rating               = excluded.rating,
            review_count         = excluded.review_count,
            prime                = excluded.prime,
            sponsored            = excluded.sponsored,
            monthly_sales        = CASE WHEN excluded.monthly_sales != ''
                                        THEN excluded.monthly_sales
                                        ELSE monthly_sales END,
            date_first_available = CASE WHEN excluded.date_first_available != ''
                                        THEN excluded.date_first_available
                                        ELSE date_first_available END,
            main_image_url       = excluded.main_image_url,
            url                  = excluded.url,
            is_new               = excluded.is_new
    """
    with _conn() as con:
        con.executemany(sql, rows)
    logger.info("Upserted %d products into DB (date=%s)", len(rows), day)


def log_run(seller_id: str, total: int, new: int,
            started_at: datetime, status: str = "success") -> None:
    day = date.today().isoformat()
    with _conn() as con:
        con.execute(
            """
            INSERT INTO scrape_runs
                (seller_id, run_date, total_products, new_products,
                 status, started_at, finished_at)
            VALUES (?,?,?,?,?,?,datetime('now','localtime'))
            ON CONFLICT(seller_id, run_date) DO UPDATE SET
                total_products = excluded.total_products,
                new_products   = excluded.new_products,
                status         = excluded.status,
                finished_at    = excluded.finished_at
            """,
            (seller_id, day, total, new, status,
             started_at.strftime("%Y-%m-%d %H:%M:%S")),
        )


# ── New-product detection query ──────────────────────────────────────────────

def get_known_asins(seller_id: str, days_back: int,
                    ref_date: date | None = None) -> set[str]:
    """Return all ASINs seen for this seller in the past `days_back` days."""
    ref = ref_date or date.today()
    from datetime import timedelta
    cutoff = (ref - timedelta(days=days_back)).isoformat()
    ref_str = ref.isoformat()
    with _conn() as con:
        rows = con.execute(
            "SELECT DISTINCT asin FROM products "
            "WHERE seller_id=? AND scraped_date BETWEEN ? AND ?",
            (seller_id, cutoff, ref_str),
        ).fetchall()
    return {r[0] for r in rows}


# ── Enrichment cache (avoid re-visiting detail pages) ────────────────────────

def get_existing_product_data(asins: list[str]) -> dict[str, dict]:
    """Return {asin: {date_first_available, monthly_sales}} for known ASINs."""
    if not asins:
        return {}
    placeholders = ",".join("?" * len(asins))
    with _conn() as con:
        rows = con.execute(
            f"SELECT asin, date_first_available, monthly_sales "
            f"FROM products WHERE asin IN ({placeholders}) "
            f"ORDER BY scraped_date DESC",
            asins,
        ).fetchall()
    result: dict[str, dict] = {}
    for asin, dfa, ms in rows:
        if asin not in result:
            result[asin] = {"date_first_available": dfa, "monthly_sales": ms or ""}
    return result


# ── Stats queries (used by GUI) ───────────────────────────────────────────────

def get_stats() -> dict:
    with _conn() as con:
        total = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        today = date.today().isoformat()
        new_today = con.execute(
            "SELECT COUNT(*) FROM products WHERE scraped_date=? AND is_new=1",
            (today,)).fetchone()[0]
        sellers = con.execute(
            "SELECT COUNT(DISTINCT seller_id) FROM products").fetchone()[0]
        last_row = con.execute(
            "SELECT MAX(scraped_date) FROM products").fetchone()[0]
        last_run = last_row or "-"
    return {
        "total_products": total,
        "new_today":      new_today,
        "sellers":        sellers,
        "last_run":       last_run,
    }
