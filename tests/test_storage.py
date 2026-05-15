"""Tests for storage module (no network required)."""
import csv
import sys
import os
from datetime import date, timedelta
from pathlib import Path

import pytest

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect DATA_DIR to a temp directory for every test."""
    from amazon_scraper import config
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    return tmp_path


SAMPLE_PRODUCTS = [
    {
        "seller_id": "SELLER1",
        "asin": "B000000001",
        "title": "Product A",
        "brand": "BrandX",
        "price": "$19.99",
        "list_price": "$24.99",
        "rating": "4.5",
        "review_count": "1,234",
        "bsr": "#1 in Widgets",
        "prime": "Yes",
        "availability": "In Stock",
        "category": "Home > Kitchen",
        "date_first_available": "January 1, 2024",
        "main_image_url": "https://images.example.com/a.jpg",
        "bullet_points": "Feature 1 | Feature 2",
        "description": "A great product.",
        "url": "https://www.amazon.com/dp/B000000001",
    },
    {
        "seller_id": "SELLER1",
        "asin": "B000000002",
        "title": "Product B",
        "brand": "BrandY",
        "price": "$9.99",
        "list_price": "",
        "rating": "3.8",
        "review_count": "42",
        "bsr": "#50 in Gadgets",
        "prime": "No",
        "availability": "In Stock",
        "category": "Electronics",
        "date_first_available": "March 5, 2024",
        "main_image_url": "https://images.example.com/b.jpg",
        "bullet_points": "Bullet A",
        "description": "Another product.",
        "url": "https://www.amazon.com/dp/B000000002",
    },
]


def test_save_and_load_snapshot():
    from amazon_scraper.storage import save_snapshot, load_snapshot

    today = date(2024, 6, 1)
    save_snapshot("SELLER1", SAMPLE_PRODUCTS, for_date=today)
    loaded = load_snapshot("SELLER1", for_date=today)

    assert len(loaded) == 2
    assert loaded[0]["asin"] == "B000000001"
    assert loaded[1]["price"] == "$9.99"


def test_load_snapshot_missing_file():
    from amazon_scraper.storage import load_snapshot

    result = load_snapshot("NONEXISTENT", for_date=date(2000, 1, 1))
    assert result == []


def test_find_new_products_first_day():
    """With no yesterday snapshot, all products should be 'new'."""
    from amazon_scraper.storage import find_new_products

    today = date(2024, 6, 1)
    new = find_new_products("SELLER1", SAMPLE_PRODUCTS, today=today)
    assert len(new) == 2


def test_find_new_products_no_change():
    """If today == yesterday, no new products."""
    from amazon_scraper.storage import save_snapshot, find_new_products

    today = date(2024, 6, 2)
    yesterday = today - timedelta(days=1)

    save_snapshot("SELLER1", SAMPLE_PRODUCTS, for_date=yesterday)
    new = find_new_products("SELLER1", SAMPLE_PRODUCTS, today=today)
    assert new == []


def test_find_new_products_detects_addition():
    """One truly new product should be flagged."""
    from amazon_scraper.storage import save_snapshot, find_new_products

    today = date(2024, 6, 2)
    yesterday = today - timedelta(days=1)

    # Yesterday: only product A
    save_snapshot("SELLER1", [SAMPLE_PRODUCTS[0]], for_date=yesterday)

    # Today: product A + product B
    new = find_new_products("SELLER1", SAMPLE_PRODUCTS, today=today)
    assert len(new) == 1
    assert new[0]["asin"] == "B000000002"


def test_append_new_products_creates_csv(tmp_path):
    from amazon_scraper.storage import append_new_products, new_products_path

    today = date(2024, 6, 2)
    path = append_new_products(SAMPLE_PRODUCTS, for_date=today)

    assert path.exists()
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["asin"] == "B000000001"


def test_append_new_products_appends():
    """Calling append twice should result in two rows, not duplicated headers."""
    from amazon_scraper.storage import append_new_products

    today = date(2024, 6, 2)
    append_new_products([SAMPLE_PRODUCTS[0]], for_date=today)
    append_new_products([SAMPLE_PRODUCTS[1]], for_date=today)

    path = __import__("amazon_scraper.storage", fromlist=["new_products_path"]).new_products_path(for_date=today)
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
