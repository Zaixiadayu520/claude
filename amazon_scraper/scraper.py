"""
Amazon scraper: seller storefront listing + product detail pages.
"""
import re
import time
import random
import logging
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
    """GET a URL with retries; return parsed BS4 or None on failure."""
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            session.headers["User-Agent"] = random.choice(USER_AGENTS)
            resp = session.get(url, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code == 503:
                logger.warning("503 on %s (attempt %d/%d) — backing off",
                               url, attempt, config.MAX_RETRIES)
                time.sleep(30 * attempt)
                continue
            if resp.status_code != 200:
                logger.warning("HTTP %s for %s", resp.status_code, url)
                return None
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as exc:
            logger.warning("Request error %s (attempt %d/%d): %s",
                           url, attempt, config.MAX_RETRIES, exc)
            time.sleep(5 * attempt)
    return None


def _sleep():
    delay = random.uniform(config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX)
    time.sleep(delay)


# ── Storefront listing ────────────────────────────────────────────────────────

def _storefront_url(seller_id: str, page: int = 1) -> str:
    return (
        f"https://www.{config.AMAZON_DOMAIN}/s"
        f"?me={seller_id}&marketplaceID={config.MARKETPLACE_ID}&page={page}"
    )


def _parse_listing_page(soup: BeautifulSoup) -> list[str]:
    """Return list of ASINs found on a search/listing results page."""
    asins: list[str] = []
    for div in soup.select("[data-asin]"):
        asin = div.get("data-asin", "").strip()
        if asin and len(asin) == 10:
            asins.append(asin)
    return list(dict.fromkeys(asins))  # deduplicate, preserve order


def _has_next_page(soup: BeautifulSoup) -> bool:
    return bool(soup.select_one("a.s-pagination-next:not(.s-pagination-disabled)"))


def get_seller_asins(seller_id: str) -> list[str]:
    """Scrape all ASINs from a seller's storefront (paginated)."""
    session = _build_session()
    all_asins: list[str] = []
    max_pages = config.MAX_PAGES_PER_SELLER or 9999

    for page in range(1, max_pages + 1):
        url = _storefront_url(seller_id, page)
        logger.info("Fetching seller %s page %d: %s", seller_id, page, url)
        soup = _get(session, url)
        if soup is None:
            logger.error("Failed to fetch page %d for seller %s", page, seller_id)
            break

        asins = _parse_listing_page(soup)
        if not asins:
            logger.info("No ASINs on page %d — stopping pagination", page)
            break

        all_asins.extend(asins)
        logger.info("  Found %d ASINs (total so far: %d)", len(asins), len(all_asins))

        if not _has_next_page(soup):
            break
        _sleep()

    return list(dict.fromkeys(all_asins))


# ── Product detail page ───────────────────────────────────────────────────────

def _product_url(asin: str) -> str:
    return f"https://www.{config.AMAZON_DOMAIN}/dp/{asin}"


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _parse_product(soup: BeautifulSoup, asin: str) -> dict:
    product: dict = {"asin": asin}

    # Title
    title_tag = soup.select_one("#productTitle")
    product["title"] = _clean(title_tag.get_text()) if title_tag else ""

    # Brand
    brand_tag = (
        soup.select_one("#bylineInfo")
        or soup.select_one(".po-brand .po-break-word")
    )
    product["brand"] = _clean(brand_tag.get_text()) if brand_tag else ""
    product["brand"] = re.sub(r"^(Brand|Visit the|Store):?\s*", "",
                               product["brand"], flags=re.IGNORECASE)

    # Price
    price_tag = (
        soup.select_one(".a-price .a-offscreen")
        or soup.select_one("#priceblock_ourprice")
        or soup.select_one("#priceblock_dealprice")
    )
    product["price"] = _clean(price_tag.get_text()) if price_tag else ""

    # List price / discount
    list_price_tag = soup.select_one(".basisPrice .a-offscreen")
    product["list_price"] = _clean(list_price_tag.get_text()) if list_price_tag else ""

    # Rating
    rating_tag = soup.select_one("#acrPopover .a-size-base.a-color-base")
    if not rating_tag:
        rating_tag = soup.select_one("span[data-hook='rating-out-of-text']")
    product["rating"] = _clean(rating_tag.get_text()) if rating_tag else ""

    # Review count
    review_count_tag = soup.select_one("#acrCustomerReviewText")
    if not review_count_tag:
        review_count_tag = soup.select_one("span[data-hook='total-review-count']")
    product["review_count"] = re.sub(r"[^\d,]", "",
        review_count_tag.get_text()) if review_count_tag else ""

    # BSR (Best Sellers Rank)
    bsr_section = soup.find("li", {"id": re.compile(r"SalesRank")}) \
                  or soup.find("tr", {"class": re.compile(r".*rank.*", re.I)})
    if not bsr_section:
        bsr_section = soup.find(string=re.compile(r"Best Sellers Rank", re.I))
        bsr_section = bsr_section.parent if bsr_section else None
    product["bsr"] = _clean(bsr_section.get_text()) if bsr_section else ""
    product["bsr"] = re.sub(r"\s+", " ", product["bsr"])[:300]

    # Date First Available
    date_tag = None
    for row in soup.select(".prodDetSectionEntry, .a-expander-content tr"):
        label = row.select_one("td:first-child, th")
        value = row.select_one("td:last-child")
        if label and value and "Date First Available" in label.get_text():
            date_tag = value
            break
    if not date_tag:
        # Try detail bullets
        for li in soup.select("#detailBullets_feature_div li"):
            text = li.get_text()
            if "Date First Available" in text:
                parts = text.split(":")
                product["date_first_available"] = _clean(parts[-1]) if len(parts) > 1 else ""
                break
    product.setdefault("date_first_available",
                        _clean(date_tag.get_text()) if date_tag else "")

    # Category (breadcrumb)
    breadcrumb = soup.select("#wayfinding-breadcrumbs_feature_div a")
    product["category"] = " > ".join(_clean(a.get_text()) for a in breadcrumb)

    # Main image URL
    img_tag = soup.select_one("#imgTagWrapperId img, #landingImage")
    product["main_image_url"] = img_tag.get("src", "") if img_tag else ""

    # Bullet points (key features)
    bullets = soup.select("#feature-bullets li span.a-list-item")
    product["bullet_points"] = " | ".join(
        _clean(b.get_text()) for b in bullets if _clean(b.get_text())
    )

    # Product description
    desc_tag = soup.select_one("#productDescription p, #productDescription")
    product["description"] = _clean(desc_tag.get_text())[:1000] if desc_tag else ""

    # Prime eligibility
    product["prime"] = "Yes" if soup.select_one(".a-icon-prime") else "No"

    # Availability
    avail_tag = soup.select_one("#availability span")
    product["availability"] = _clean(avail_tag.get_text()) if avail_tag else ""

    # Product URL
    product["url"] = _product_url(asin)

    return product


def get_product_details(asin: str, session: Optional[requests.Session] = None) -> Optional[dict]:
    """Fetch and parse a single product detail page."""
    if session is None:
        session = _build_session()
    url = _product_url(asin)
    logger.info("  Fetching product %s", asin)
    soup = _get(session, url)
    if soup is None:
        logger.error("  Failed to fetch product %s", asin)
        return None
    return _parse_product(soup, asin)


def scrape_seller(seller_id: str) -> list[dict]:
    """
    Full pipeline for one seller:
    1. Collect all ASINs from the storefront.
    2. Fetch detail page for each ASIN.
    Returns list of product dicts with a 'seller_id' field added.
    """
    logger.info("=== Scraping seller %s ===", seller_id)
    asins = get_seller_asins(seller_id)
    logger.info("Total ASINs found for %s: %d", seller_id, len(asins))

    session = _build_session()
    products: list[dict] = []

    for i, asin in enumerate(asins, 1):
        logger.info("[%d/%d] Getting details for ASIN %s", i, len(asins), asin)
        product = get_product_details(asin, session)
        if product:
            product["seller_id"] = seller_id
            products.append(product)
        _sleep()

    logger.info("Seller %s: collected %d products", seller_id, len(products))
    return products
