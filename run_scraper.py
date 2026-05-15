"""
Standalone runner. Supports two modes:

  python run_scraper.py            # single run now
  python run_scraper.py --schedule # block and run daily at SCHEDULE_TIME
"""
import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Amazon new-product scraper")
    parser.add_argument(
        "--schedule", action="store_true",
        help="Keep running and execute daily at config.SCHEDULE_TIME",
    )
    parser.add_argument(
        "--sellers", nargs="+", metavar="SELLER_ID",
        help="Override seller IDs from config (space-separated list)",
    )
    args = parser.parse_args()

    if args.schedule:
        _run_scheduled(args.sellers)
    else:
        from amazon_scraper.main import run_once
        run_once(args.sellers)


def _run_scheduled(seller_ids) -> None:
    try:
        import schedule
    except ImportError:
        print("ERROR: 'schedule' package not installed. Run: pip install schedule")
        sys.exit(1)

    import time
    from amazon_scraper import config
    from amazon_scraper.main import run_once

    def job():
        run_once(seller_ids)

    schedule.every().day.at(config.SCHEDULE_TIME).do(job)
    print(f"Scheduler started. Will run daily at {config.SCHEDULE_TIME}.")
    print("Press Ctrl-C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
