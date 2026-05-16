"""
Amazon scraper — optimised.

Phase 1: Extract basic data from listing pages (concurrent, fast).
Phase 2: Fetch product detail pages for date_first_available + monthly_sales
         (concurrent, polite delays, skips products already enriched in DB).
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
_log_lock = threading.Lock()

# ── HTTP helpers ──────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def _build_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1",
    })
    # Force USD pricing regardless of geo-IP location
    s.cookies.set("i18n-prefs",       "USD",  domain=".amazon.com")
    s.cookies.set("lc-main",          "en_US", domain=".amazon.com")
    s.cookies.set("sp-cdn",           "L5Z68:CN", domain=".amazon.com")
    if config.PROXY:
        s.proxies = {"http": config.PROXY, "https": config.PROXY}
    return s


def _is_bot_check(soup: BeautifulSoup) -> bool:
    """Return True if Amazon returned a CAPTCHA / robot-check page."""
    title = soup.find("title")
    t = (title.get_text() if title else "").lower()
    if "robot" in t or "captcha" in t or "validatecaptcha" in t:
        return True
    if soup.find("form", {"action": lambda a: a and "validateCaptcha" in a}):
        return True
    if "api-services-support.amazon.com" in (soup.get_text()[:500]):
        return True
    return False


def _get(session: requests.Session, url: str,
         extra_params: str = "") -> Optional[BeautifulSoup]:
    full_url = url + extra_params if extra_params else url
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            session.headers["User-Agent"] = random.choice(USER_AGENTS)
            resp = session.get(full_url, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 503:
                wait = 20 * attempt
                logger.warning("503 — backing off %ds (attempt %d/%d)",
                               wait, attempt, config.MAX_RETRIES)
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                logger.warning("HTTP %s for %s", resp.status_code, full_url)
                return None
            soup = BeautifulSoup(resp.text, "lxml")
            if _is_bot_check(soup):
                title = soup.find("title")
                logger.warning(
                    "⚠ Amazon 返回反爬验证页（attempt %d/%d）页面标题: %s  "
                    "请检查：1.代理是否设置为全局模式  2.更换代理节点  3.稍后重试",
                    attempt, config.MAX_RETRIES,
                    title.get_text().strip() if title else "unknown")
                time.sleep(15 * attempt)
                continue
            return soup
        except requests.RequestException as exc:
            logger.warning("Request error (attempt %d/%d): %s",
                           attempt, config.MAX_RETRIES, exc)
            time.sleep(3 * attempt)
    return None


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


_MONTH_MAP = {
    "january":"01","february":"02","march":"03","april":"04",
    "may":"05","june":"06","july":"07","august":"08",
    "september":"09","october":"10","november":"11","december":"12",
}


def _normalize_date(raw: str) -> str:
    """Convert 'November 9, 2022' -> '2022-11-09'. Returns raw if unparseable."""
    s = raw.strip()
    if not s:
        return ""
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    # "Month D, YYYY" or "Month DD YYYY"
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", s)
    if m:
        mo = _MONTH_MAP.get(m.group(1).lower())
        if mo:
            return f"{m.group(3)}-{mo}-{m.group(2).zfill(2)}"
    return s


# ── Phase 1: listing page extraction ─────────────────────────────────────────

def _storefront_url(seller_id: str, page: int = 1) -> str:
    return (
        f"https://www.{config.AMAZON_DOMAIN}/s"
        f"?me={seller_id}&marketplaceID={config.MARKETPLACE_ID}"
        f"&s=date-desc-rank&currency=USD&language=en_US&page={page}"
    )


def _parse_price(card: BeautifulSoup) -> tuple[str, str]:
    """Return (price, list_price) both in USD ($X.XX format)."""
    prices: list[str] = []
    for tag in card.select(".a-price .a-offscreen"):
        val = _clean(tag.get_text())
        if val:
            prices.append(val)

    # Prefer the first price that looks like USD ($)
    def _is_usd(p: str) -> bool:
        return p.startswith("$") or bool(re.match(r"\$[\d,]+", p))

    usd_prices = [p for p in prices if _is_usd(p)]
    all_prices  = usd_prices if usd_prices else prices

    price      = all_prices[0] if len(all_prices) > 0 else ""
    list_price = ""
    for p in all_prices[1:]:
        if p != price:
            list_price = p
            break
    return price, list_price


def _parse_monthly_sales_from_card(card: BeautifulSoup) -> str:
    """Extract 'X+ bought in past month' badge from a listing card."""
    # Walk every text node in the card
    full_text = card.get_text(" ", strip=True)
    # Patterns: "1K+ bought in past month", "500+ bought in past month", "1,000+ bought in past month"
    m = re.search(
        r"([\d,]+(?:\.\d+)?[KkMm]?\+?)\s+bought in past month",
        full_text, re.IGNORECASE,
    )
    if m:
        raw = m.group(1).strip().rstrip("+")
        # Normalise K/M suffixes -> plain number with + suffix
        raw_up = raw.upper().replace(",", "")
        if raw_up.endswith("K"):
            num = int(float(raw_up[:-1]) * 1000)
            return f"{num:,}+"
        if raw_up.endswith("M"):
            num = int(float(raw_up[:-1]) * 1_000_000)
            return f"{num:,}+"
        return raw + "+"
    return ""


def _parse_cards(soup: BeautifulSoup, seller_id: str) -> list[dict]:
    products: list[dict] = []

    for card in soup.select("div[data-asin][data-component-type='s-search-result']"):
        asin = card.get("data-asin", "").strip()
        if not asin or len(asin) != 10:
            continue

        # Title
        title_tag = card.select_one("h2 span, h2 a span")
        title = _clean(title_tag.get_text()) if title_tag else ""

        # Brand
        brand = ""
        for brand_tag in card.select(".a-size-base.a-color-secondary"):
            raw = _clean(brand_tag.get_text())
            if raw and not re.search(r"\$|bought|star|rating", raw, re.I):
                brand = re.sub(r"^by\s+", "", raw, flags=re.IGNORECASE)
                if brand:
                    break

        # Price (USD forced via cookie + URL param)
        price, list_price = _parse_price(card)

        # Rating
        rating = ""
        for rtag in card.select("span[aria-label]"):
            m = re.search(r"([\d.]+)\s+out of\s+5", rtag.get("aria-label", ""))
            if m:
                rating = m.group(1)
                break
        if not rating:
            alt = card.select_one(".a-icon-alt")
            if alt:
                m = re.search(r"([\d.]+)", _clean(alt.get_text()))
                if m:
                    rating = m.group(1)

        # Review count
        review_count = ""
        for rtag in card.select("span[aria-label]"):
            lbl = rtag.get("aria-label", "")
            m = re.search(r"([\d,]+)\s+rating", lbl, re.IGNORECASE)
            if m:
                review_count = m.group(1)
                break
        if not review_count:
            rc = card.select_one(".a-size-base.s-underline-text")
            if rc:
                review_count = re.sub(r"[^\d,]", "", _clean(rc.get_text()))

        # Prime
        prime = "Yes" if card.select_one(".a-icon-prime") else "No"

        # Monthly sales from listing card
        monthly_sales = _parse_monthly_sales_from_card(card)

        # Image
        img = card.select_one("img.s-image")
        image_url = img.get("src", "") if img else ""

        # Clean product URL (strip tracking params, force en_US)
        link = card.select_one("h2 a[href]")
        href = link.get("href", f"/dp/{asin}") if link else f"/dp/{asin}"
        if href.startswith("/"):
            href = f"https://www.{config.AMAZON_DOMAIN}{href}"
        url = re.sub(r"\?.*", "", href) or f"https://www.{config.AMAZON_DOMAIN}/dp/{asin}"

        # Sponsored
        sponsored = "Yes" if card.select_one(
            ".s-label-popover-default, [class*='AdHolder'], "
            "span[data-component-type='s-status-badge-component']"
        ) else "No"

        products.append({
            "seller_id":            seller_id,
            "asin":                 asin,
            "title":                title,
            "brand":                brand,
            "price":                price,
            "list_price":           list_price,
            "rating":               rating,
            "review_count":         review_count,
            "prime":                prime,
            "sponsored":            sponsored,
            "monthly_sales":        monthly_sales,
            "date_first_available": "",
            "main_image_url":       image_url,
            "url":                  url,
        })

    return products


def _has_next_page(soup: BeautifulSoup) -> bool:
    return bool(soup.select_one("a.s-pagination-next:not(.s-pagination-disabled)"))


def _total_pages(soup: BeautifulSoup, max_pages: int) -> int:
    nums = [int(t.get_text()) for t in soup.select("span.s-pagination-item")
            if t.get_text().isdigit()]
    found = max(nums) if nums else 1
    return min(found, max_pages) if max_pages else found


def _fetch_page(seller_id: str, page: int) -> tuple[int, list[dict], bool]:
    session = _build_session()
    time.sleep(random.uniform(0.3, 1.2) * (page % max(config.CONCURRENT_PAGES, 1)))
    url   = _storefront_url(seller_id, page)
    soup  = _get(session, url)
    if soup is None:
        return page, [], False
    products = _parse_cards(soup, seller_id)
    has_next = _has_next_page(soup)
    with _log_lock:
        logger.info("  第%d页：提取 %d 个产品", page, len(products))
    return page, products, has_next


def scrape_seller(seller_id: str) -> list[dict]:
    from .storage import safe_seller_id
    seller_id = safe_seller_id(seller_id)
    logger.info("=== 开始采集店铺 %s ===", seller_id)
    max_pages = config.MAX_PAGES_PER_SELLER or 999
    workers   = min(config.CONCURRENT_PAGES, max_pages)

    session_p1 = _build_session()
    soup1 = _get(session_p1, _storefront_url(seller_id, 1))
    if soup1 is None:
        logger.error("无法访问店铺 %s 的页面", seller_id)
        return []

    page1_products = _parse_cards(soup1, seller_id)
    logger.info("  第1页：提取 %d 个产品", len(page1_products))

    if len(page1_products) == 0:
        # Check if the page has any search result containers at all
        any_cards = soup1.select("div[data-asin]")
        if not any_cards:
            logger.warning(
                "⚠ 第1页未找到任何商品卡片（data-asin 元素为0）。"
                "可能原因：1.代理未开启全局模式  2.Amazon 反爬拦截  3.卖家ID有误。"
                "页面标题: %s",
                (soup1.find("title") or {}).get_text("").strip() if soup1.find("title") else "N/A"
            )

    if not _has_next_page(soup1):
        if len(page1_products) == 0:
            logger.warning("单页且0产品，采集中止。")
        else:
            logger.info("单页店铺，采集完成。")
        return page1_products

    total = _total_pages(soup1, max_pages)
    logger.info("并发采集第 2-%d 页（%d 个线程）", total, workers)

    all_products: list[dict] = list(page1_products)
    seen_asins: set[str] = {p["asin"] for p in page1_products}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_page, seller_id, pg): pg
                   for pg in range(2, total + 1)}
        for future in as_completed(futures):
            _, products, _ = future.result()
            for p in products:
                if p["asin"] not in seen_asins:
                    seen_asins.add(p["asin"])
                    all_products.append(p)

    logger.info("店铺 %s：共 %d 个唯一产品", seller_id, len(all_products))
    return all_products


# ── Phase 2: detail page — date + monthly sales ───────────────────────────────

def _detail_url(asin: str) -> str:
    """Force English + USD on the detail page."""
    return (f"https://www.{config.AMAZON_DOMAIN}/dp/{asin}"
            f"?language=en_US&currency=USD&th=1&psc=1")


def _extract_date_first_available(soup: BeautifulSoup) -> str:
    """Try every known HTML pattern for 'Date First Available'."""
    keyword = "Date First Available"

    # Pattern 1: #detailBullets_feature_div  (most common)
    for li in soup.select("#detailBullets_feature_div li"):
        text = li.get_text(" ", strip=True)
        if keyword in text:
            # Remove the label, keep only the date part
            date_part = re.sub(r".*Date First Available\s*[:]*\s*", "", text,
                               flags=re.IGNORECASE).strip()
            if date_part:
                return _normalize_date(date_part)

    # Pattern 2: product details table rows
    for row in soup.select("tr"):
        cells = row.select("td, th")
        for i, cell in enumerate(cells):
            if keyword in cell.get_text():
                if i + 1 < len(cells):
                    return _normalize_date(_clean(cells[i + 1].get_text()))

    # Pattern 3: tech details table (some categories)
    for row in soup.select(".a-keyvalue tr, .prodDetTable tr"):
        label = row.select_one("th, td:first-child")
        value = row.select_one("td:last-child")
        if label and value and keyword in label.get_text():
            return _normalize_date(_clean(value.get_text()))

    # Pattern 4: plain text regex fallback
    m = re.search(
        r"Date First Available\s*[:]*\s*([A-Za-z]+ \d{1,2},\s*\d{4})",
        soup.get_text(), re.IGNORECASE,
    )
    return _normalize_date(_clean(m.group(1))) if m else ""


def _extract_monthly_sales_detail(soup: BeautifulSoup) -> str:
    """Extract monthly sales from a product detail page (more reliable than listing page)."""
    full_text = soup.get_text(" ", strip=True)
    m = re.search(
        r"([\d,]+(?:\.\d+)?[KkMm]?\+?)\s+(?:purchased|bought)\s+(?:in\s+)?(?:the\s+)?past\s+month",
        full_text, re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"([\d,]+[KkMm]?)\+?\s+people\s+(?:purchased|bought)",
            full_text, re.IGNORECASE,
        )
    if not m:
        # Amazon sometimes shows it as a badge in a specific element
        for tag in soup.select(
            "span.social-proofing-faceout-title-text, "
            "span[id*='social-proof'], "
            "span[data-csa-c-content-id*='social'], "
            ".social-proofing-faceout span, "
            "#socialProofingAsinFaceout_feature_div span"
        ):
            badge_m = re.search(
                r"([\d,KkMm]+\+?)\s+(?:purchased|bought)", tag.get_text(), re.I)
            if badge_m:
                m = badge_m
                break
    if not m:
        return ""
    raw = m.group(1).strip().rstrip("+").replace(",", "").upper()
    if raw.endswith("K"):
        return f"{int(float(raw[:-1]) * 1000):,}+"
    if raw.endswith("M"):
        return f"{int(float(raw[:-1]) * 1_000_000):,}+"
    try:
        return f"{int(raw):,}+"
    except ValueError:
        return raw + "+"


def batch_enrich_products(products: list[dict],
                           max_workers: int = 2,
                           progress_cb=None) -> None:
    """
    Visit each product's detail page to fill in:
      - date_first_available  (always needed)
      - monthly_sales         (if not already found on listing page)

    Skips products that already have date_first_available set.
    Modifies list in-place.
    Each worker reuses a persistent session to reduce connection overhead.
    """
    needed = [p for p in products if not p.get("date_first_available")]
    total  = len(needed)
    if not total:
        logger.info("所有产品已有上架日期，跳过详情页采集。")
        return

    logger.info("开始采集详情页：%d 个产品（%d 个并发线程）...", total, max_workers)
    done_count = [0]

    # Each worker thread gets its own persistent session (thread-local)
    _tls = threading.local()

    def _session() -> requests.Session:
        if not hasattr(_tls, "sess"):
            _tls.sess = _build_session()
        return _tls.sess

    def _fetch_one(product: dict) -> None:
        asin = product.get("asin", "")
        if not asin:
            return
        soup = _get(_session(), _detail_url(asin))
        if soup:
            date_str = _extract_date_first_available(soup)
            if date_str:
                product["date_first_available"] = date_str
            if not product.get("monthly_sales"):
                sales = _extract_monthly_sales_detail(soup)
                if sales:
                    product["monthly_sales"] = sales

        with _log_lock:
            done_count[0] += 1
            n = done_count[0]
            logger.info("  详情页 [%d/%d] ASIN:%s  上架:%s  月销:%s",
                        n, total, asin,
                        product.get("date_first_available", "-"),
                        product.get("monthly_sales", "-"))
            if progress_cb:
                progress_cb(n, total)

        time.sleep(random.uniform(1.0, 2.0))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_one, p) for p in needed]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                logger.warning("详情页采集出错: %s", exc)

    found_dates = sum(1 for p in needed if p.get("date_first_available"))
    found_sales = sum(1 for p in needed if p.get("monthly_sales"))
    logger.info("详情页采集完成：%d/%d 获得上架日期，%d/%d 获得月销量",
                found_dates, total, found_sales, total)


# Keep old name as alias for main.py compatibility
def fetch_listing_dates(products, max_workers=3):
    batch_enrich_products(products, max_workers=max_workers)
