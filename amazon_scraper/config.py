"""
Configuration for Amazon product scraper.
Edit SELLER_IDS and other settings before running.
"""

# ── Sellers to monitor ────────────────────────────────────────────────────────
# Add Amazon Seller IDs here (visible in the seller's storefront URL: ?me=XXXXX)
SELLER_IDS = [
    "AO15CCGYGZREK",
]

# ── Site settings ─────────────────────────────────────────────────────────────
AMAZON_DOMAIN = "amazon.com"
MARKETPLACE_ID = "ATVPDKIKX0ER"   # US marketplace

# ── New-product detection ─────────────────────────────────────────────────────
# Products are flagged as "new" based on their date_first_available field.
# Default range: past 30 days.  Overridden by GUI listing date selector.
NEW_PRODUCT_DAYS = 30  # kept for CLI backward compatibility

# ── Output ────────────────────────────────────────────────────────────────────
DATA_DIR = "data"           # daily snapshots and output CSVs go here
LOG_DIR  = "logs"

# ── Scraping behaviour ────────────────────────────────────────────────────────
# Detail pages are no longer visited — only listing pages are fetched.
# Concurrent pages: how many listing pages to fetch simultaneously.
CONCURRENT_PAGES        = 4   # listing pages fetched in parallel
CONCURRENT_DETAIL_PAGES = 1   # detail page workers — keep at 1 to avoid bot blocks
MAX_PAGES_PER_SELLER    = 20  # safety cap; set 0 for unlimited
REQUEST_TIMEOUT         = 25
MAX_RETRIES             = 3

# Optional HTTP proxy (e.g. "http://user:pass@host:port"). Leave "" to disable.
PROXY = ""

# ── Schedule ──────────────────────────────────────────────────────────────────
# Daily run time in 24-h HH:MM format (local time).  Used by the built-in
# scheduler; irrelevant when triggered by an external cron job.
SCHEDULE_TIME = "08:00"
