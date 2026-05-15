"""
Amazon scraper — fast mode.

Extracts all basic product data directly from the seller storefront
listing pages. No individual product detail pages are visited, which
cuts request count from O(products) down to O(pages).

Multiple pages are fetched concurrently up to CONCURRENT_PAGES workers.
"""
import re
import time
import random
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests
from bs4 import BeautifulSoup

from . import config

logger = logging.getLogger(__name__)

# ── HTTP helpers ──────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Shared lock so concurrent threads don't stomp each other's log lines
_log_lock = threading.Lock()


def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    })
    if config.PROXY:
        s.proxies = {"http": config.PROXY, "https": config.PROXY}
    return s


def _get(session: requests.Session, url: str) -> Optional[BeautifulSoup]:
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            session.headers["User-Agent"] = random.choice(USER_AGENTS)
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 503:
                wait = 20 * attempt
                logger.warning("503 — backing off %ds (attempt %d/%d)",
                               wait, attempt, config.MAX_RETRIES)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                logger.warning("HTTP %s for %s", resp.status_code, url)
                return None
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as exc:
            logger.warning("Request error (attempt %d/%d): %s", attempt, config.MAX_RETRIES, exc)
            time.sleep(3 * attempt)
    return None


# ── Listing page parsing ──────────────────────────────────────────────────────

def _storefront_url(seller_id: str, page: int = 1) -> str:
    return (
        f"https://www.{config.AMAZON_DOMAIN}/s"
        f"?me={seller_id}&marketplaceID={config.MARKETPLACE_ID}&page={page}"
    )


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _parse_cards(soup: BeautifulSoup, seller_id: str) -> list[dict]:
    """
    Extract one product dict per card directly from a listing results page.
    Covers all basic fields without visiting individual product pages.
    """
    products: list[dict] = []

    for card in soup.select("div[data-asin][data-component-type='s-search-result']"):
        asin = card.get("data-asin", "").strip()
        if not asin or len(asin) != 10:
            continue

        # Title
        title_tag = card.select_one("h2 span, h2 a span")
        title = _clean(title_tag.get_text()) if title_tag else ""

        # Brand — shown as "by BrandName" or in a separate span
        brand = ""
        brand_tag = card.select_one(".a-row .a-size-base.a-color-secondary")
        if brand_tag:
            raw = _clean(brand_tag.get_text())
            brand = re.sub(r"^by\s+", "", raw, flags=re.IGNORECASE)

        # Price
        price = ""
        price_tag = card.select_one(".a-price .a-offscreen")
        if price_tag:
            price = _clean(price_tag.get_text())

        # Original / list price
        list_price = ""
        list_tags = card.select(".a-price.a-text-price .a-offscreen")
        for lt in list_tags:
            candidate = _clean(lt.get_text())
            if candidate and candidate != price:
                list_price = candidate
                break

        # Rating (e.g. "4.5 out of 5 stars")
        rating = ""
        rating_tag = card.select_one("span[aria-label*='out of']")
        if rating_tag:
            m = re.search(r"([\d.]+)\s+out of", rating_tag.get("aria-label", ""))
            rating = m.group(1) if m else ""
        if not rating:
            rating_tag2 = card.select_one(".a-icon-alt")
            if rating_tag2:
                m = re.search(r"([\d.]+)", _clean(rating_tag2.get_text()))
                rating = m.group(1) if m else ""

        # Review count
        review_count = ""
        rc_tag = card.select_one("span[aria-label].a-size-base")
        if rc_tag:
            label = rc_tag.get("aria-label", "")
            m = re.search(r"([\d,]+)", label)
            review_count = m.group(1) if m else ""
        if not review_count:
            rc_tag2 = card.select_one(".a-size-base.s-underline-text")
            if rc_tag2:
                review_count = re.sub(r"[^\d,]", "", _clean(rc_tag2.get_text()))

        # Prime
        prime = "Yes" if card.select_one(".a-icon-prime") else "No"

        # Main image
        img_tag = card.select_one("img.s-image")
        image_url = img_tag.get("src", "") if img_tag else ""

        # Product URL
        link_tag = card.select_one("h2 a[href]")
        href = link_tag.get("href", "") if link_tag else f"/dp/{asin}"
        if href.startswith("/"):
            href = f"https://www.{config.AMAZON_DOMAIN}{href}"
        url = re.sub(r"\?.*", "", href) or f"https://www.{config.AMAZON_DOMAIN}/dp/{asin}"

        # Sponsored flag
        sponsored = "Yes" if card.select_one(".s-label-popover-default, [class*='AdHolder']") else "No"

        products.append({
            "seller_id":    seller_id,
            "asin":         asin,
            "title":        title,
            "brand":        brand,
            "price":        price,
            "list_price":   list_price,
            "rating":       rating,
            "review_count": review_count,
            "prime":        prime,
            "sponsored":    sponsored,
            "main_image_url": image_url,
            "url":          url,
        })

    return products


def _has_next_page(soup: BeautifulSoup) -> bool:
    return bool(soup.select_one("a.s-pagination-next:not(.s-pagination-disabled)"))


def _total_pages(soup: BeautifulSoup, max_pages: int) -> int:
    """Best-effort total page count from pagination widget."""
    nums = [
        int(t.get_text())
        for t in soup.select("span.s-pagination-item")
        if t.get_text().isdigit()
    ]
    found = max(nums) if nums else 1
    return min(found, max_pages) if max_pages else found


# ── Concurrent multi-page fetch ───────────────────────────────────────────────

def _fetch_page(seller_id: str, page: int) -> tuple[int, list[dict], bool]:
    """Fetch one listing page; return (page_num, products, has_next)."""
    session = _build_session()
    # Stagger concurrent requests slightly to reduce burst fingerprint
    time.sleep(random.uniform(0.5, 1.5) * (page % config.CONCURRENT_PAGES))
    url = _storefront_url(seller_id, page)
    soup = _get(session, url)
    if soup is None:
        return page, [], False
    products = _parse_cards(soup, seller_id)
    has_next = _has_next_page(soup)
    with _log_lock:
        logger.info("  Page %d: %d products extracted", page, len(products))
    return page, products, has_next


def scrape_seller(seller_id: str) -> list[dict]:
    """
    Scrape all pages of a seller's storefront concurrently.
    Returns deduplicated list of product dicts.
    """
    logger.info("=== Scraping seller %s ===", seller_id)
    max_pages = config.MAX_PAGES_PER_SELLER or 999
    workers   = min(config.CONCURRENT_PAGES, max_pages)

    # ── Phase 1: fetch page 1 to learn total pages ────────────────────────────
    session_p1 = _build_session()
    soup1 = _get(session_p1, _storefront_url(seller_id, 1))
    if soup1 is None:
        logger.error("Could not reach storefront for seller %s", seller_id)
        return []

    page1_products = _parse_cards(soup1, seller_id)
    logger.info("  Page 1: %d products extracted", len(page1_products))

    if not _has_next_page(soup1):
        logger.info("Single page seller — done.")
        return page1_products

    total = _total_pages(soup1, max_pages)
    logger.info("Fetching pages 2–%d concurrently (%d workers)", total, workers)

    # ── Phase 2: fetch remaining pages in parallel ────────────────────────────
    all_products: list[dict] = list(page1_products)
    seen_asins: set[str] = {p["asin"] for p in page1_products}

    remaining_pages = list(range(2, total + 1))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_page, seller_id, pg): pg
            for pg in remaining_pages
        }
        for future in as_completed(futures):
            _, products, _ = future.result()
            for p in products:
                if p["asin"] not in seen_asins:
                    seen_asins.add(p["asin"])
                    all_products.append(p)

    logger.info("Seller %s: %d unique products total", seller_id, len(all_products))
    return all_products
