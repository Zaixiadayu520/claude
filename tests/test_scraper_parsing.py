"""Tests for HTML parsing logic (no network required)."""
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))

from amazon_scraper.scraper import _parse_listing_page, _parse_product, _has_next_page


# ── _parse_listing_page ───────────────────────────────────────────────────────

LISTING_HTML = """
<html><body>
  <div data-asin="B000000001" class="s-result-item"></div>
  <div data-asin="B000000002" class="s-result-item"></div>
  <div data-asin="" class="s-result-item"></div>
  <div data-asin="BADASIN" class="s-result-item"></div>
</body></html>
"""


def test_parse_listing_page_extracts_valid_asins():
    soup = BeautifulSoup(LISTING_HTML, "lxml")
    asins = _parse_listing_page(soup)
    assert "B000000001" in asins
    assert "B000000002" in asins
    # Empty string and 7-char ASIN should be excluded
    assert "" not in asins
    assert "BADASIN" not in asins


def test_parse_listing_page_deduplicates():
    html = """
    <html><body>
      <div data-asin="B000000001"></div>
      <div data-asin="B000000001"></div>
    </body></html>
    """
    soup = BeautifulSoup(html, "lxml")
    asins = _parse_listing_page(soup)
    assert asins.count("B000000001") == 1


def test_parse_listing_page_empty():
    soup = BeautifulSoup("<html><body></body></html>", "lxml")
    assert _parse_listing_page(soup) == []


# ── _has_next_page ────────────────────────────────────────────────────────────

def test_has_next_page_true():
    html = '<html><body><a class="s-pagination-next">Next</a></body></html>'
    soup = BeautifulSoup(html, "lxml")
    assert _has_next_page(soup) is True


def test_has_next_page_disabled():
    html = '<html><body><a class="s-pagination-next s-pagination-disabled">Next</a></body></html>'
    soup = BeautifulSoup(html, "lxml")
    assert _has_next_page(soup) is False


def test_has_next_page_absent():
    soup = BeautifulSoup("<html><body></body></html>", "lxml")
    assert _has_next_page(soup) is False


# ── _parse_product ────────────────────────────────────────────────────────────

PRODUCT_HTML = """
<html><body>
  <span id="productTitle">  Test Product Title  </span>
  <a id="bylineInfo">Brand: SuperBrand</a>
  <div class="a-price"><span class="a-offscreen">$29.99</span></div>
  <span id="acrPopover"><span class="a-size-base a-color-base">4.3</span></span>
  <span id="acrCustomerReviewText">1,500 ratings</span>
  <div id="availability"><span>In Stock</span></div>
  <div id="wayfinding-breadcrumbs_feature_div">
    <a>Electronics</a> <a>Cameras</a>
  </div>
  <img id="landingImage" src="https://images.example.com/product.jpg" />
  <div id="feature-bullets">
    <ul>
      <li><span class="a-list-item">Great feature one</span></li>
      <li><span class="a-list-item">Great feature two</span></li>
    </ul>
  </div>
  <div id="productDescription"><p>This is the product description.</p></div>
  <i class="a-icon a-icon-prime"></i>
</body></html>
"""


def test_parse_product_title():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert p["title"] == "Test Product Title"


def test_parse_product_brand():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert "SuperBrand" in p["brand"]


def test_parse_product_price():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert p["price"] == "$29.99"


def test_parse_product_rating():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert p["rating"] == "4.3"


def test_parse_product_review_count():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert "1,500" in p["review_count"]


def test_parse_product_category():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert p["category"] == "Electronics > Cameras"


def test_parse_product_image_url():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert "product.jpg" in p["main_image_url"]


def test_parse_product_bullet_points():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert "Great feature one" in p["bullet_points"]
    assert "Great feature two" in p["bullet_points"]


def test_parse_product_description():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert "product description" in p["description"]


def test_parse_product_prime():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert p["prime"] == "Yes"


def test_parse_product_no_prime():
    html = PRODUCT_HTML.replace('<i class="a-icon a-icon-prime"></i>', "")
    soup = BeautifulSoup(html, "lxml")
    p = _parse_product(soup, "B000000001")
    assert p["prime"] == "No"


def test_parse_product_url():
    soup = BeautifulSoup(PRODUCT_HTML, "lxml")
    p = _parse_product(soup, "B000000001")
    assert "B000000001" in p["url"]


def test_parse_product_missing_fields():
    """Empty page should return a dict with empty strings, not raise."""
    soup = BeautifulSoup("<html><body></body></html>", "lxml")
    p = _parse_product(soup, "B000000099")
    assert p["asin"] == "B000000099"
    assert p["title"] == ""
    assert p["price"] == ""
