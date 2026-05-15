"""Tests for listing-page parsing (no network required)."""
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from amazon_scraper.scraper import _parse_cards, _has_next_page


# ── Shared test HTML ──────────────────────────────────────────────────────────

def _make_card(asin="B000000001", title="Test Product", brand="SuperBrand",
               price="$29.99", list_price="$39.99", rating="4.3",
               reviews="1,234", prime=True, sponsored=False):
    prime_html = '<i class="a-icon a-icon-prime"></i>' if prime else ""
    sponsored_html = '<span class="s-label-popover-default">Sponsored</span>' if sponsored else ""
    return f"""
    <div data-asin="{asin}"
         data-component-type="s-search-result"
         class="s-result-item">
      {sponsored_html}
      <h2>
        <a href="/dp/{asin}?ref=test">
          <span>{title}</span>
        </a>
      </h2>
      <div class="a-row">
        <span class="a-size-base a-color-secondary">by {brand}</span>
      </div>
      <span aria-label="{rating} out of 5 stars" class="a-icon-star-small">
        <span class="a-icon-alt">{rating} out of 5 stars</span>
      </span>
      <span aria-label="{reviews}" class="a-size-base s-underline-text">{reviews}</span>
      <div class="a-price">
        <span class="a-offscreen">{price}</span>
      </div>
      <div class="a-price a-text-price">
        <span class="a-offscreen">{list_price}</span>
      </div>
      {prime_html}
      <img class="s-image" src="https://images.example.com/{asin}.jpg" />
    </div>
    """


LISTING_HTML = f"""
<html><body>
  {_make_card("B000000001", "Product Alpha", "BrandA", "$19.99", "$24.99", "4.5", "500")}
  {_make_card("B000000002", "Product Beta",  "BrandB", "$9.99",  "",       "3.8", "42", prime=False)}
  <div data-asin="" data-component-type="s-search-result"></div>
  <div data-asin="SHORT" data-component-type="s-search-result"></div>
</body></html>
"""


# ── _parse_cards ──────────────────────────────────────────────────────────────

def test_parse_cards_count():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert len(products) == 2


def test_parse_cards_asin():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    asins = [p["asin"] for p in products]
    assert "B000000001" in asins
    assert "B000000002" in asins


def test_parse_cards_title():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert products[0]["title"] == "Product Alpha"


def test_parse_cards_brand():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert products[0]["brand"] == "BrandA"


def test_parse_cards_price():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert products[0]["price"] == "$19.99"


def test_parse_cards_list_price():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert products[0]["list_price"] == "$24.99"


def test_parse_cards_rating():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert products[0]["rating"] == "4.5"


def test_parse_cards_review_count():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert "500" in products[0]["review_count"]


def test_parse_cards_prime_yes():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert products[0]["prime"] == "Yes"


def test_parse_cards_prime_no():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert products[1]["prime"] == "No"


def test_parse_cards_image_url():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert "B000000001.jpg" in products[0]["main_image_url"]


def test_parse_cards_url_contains_asin():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER1")
    assert "B000000001" in products[0]["url"]


def test_parse_cards_seller_id():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    products = _parse_cards(soup, "SELLER_X")
    assert all(p["seller_id"] == "SELLER_X" for p in products)


def test_parse_cards_sponsored_yes():
    html = f"<html><body>{_make_card('B000000003', sponsored=True)}</body></html>"
    soup = BeautifulSoup(html, "lxml")
    products = _parse_cards(soup, "S1")
    assert products[0]["sponsored"] == "Yes"


def test_parse_cards_sponsored_no():
    html = f"<html><body>{_make_card('B000000004', sponsored=False)}</body></html>"
    soup = BeautifulSoup(html, "lxml")
    products = _parse_cards(soup, "S1")
    assert products[0]["sponsored"] == "No"


def test_parse_cards_empty_page():
    soup = BeautifulSoup("<html><body></body></html>", "lxml")
    assert _parse_cards(soup, "S1") == []


def test_parse_cards_skips_invalid_asins():
    html = """
    <html><body>
      <div data-asin="" data-component-type="s-search-result"></div>
      <div data-asin="TOOSHORT" data-component-type="s-search-result"></div>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    assert _parse_cards(soup, "S1") == []


# ── _has_next_page ────────────────────────────────────────────────────────────

def test_has_next_page_true():
    html = '<html><body><a class="s-pagination-next">Next</a></body></html>'
    assert _has_next_page(BeautifulSoup(html, "lxml")) is True


def test_has_next_page_disabled():
    html = '<html><body><a class="s-pagination-next s-pagination-disabled"></a></body></html>'
    assert _has_next_page(BeautifulSoup(html, "lxml")) is False


def test_has_next_page_absent():
    assert _has_next_page(BeautifulSoup("<html></html>", "lxml")) is False
