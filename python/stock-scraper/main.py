"""Entry point: log in once with a persistent browser profile, then scrape tickers to CSV."""

import sys

from selenium.common.exceptions import TimeoutException

from stockscraper.argparser import parse_arguments
from stockscraper.browser import create_driver
from stockscraper.sourceinfo import write_csv
from stockscraper.sources import SOURCES


def main(argv: list[str] | None = None) -> int:
    """Runs the scraper and returns a process exit code."""
    args = parse_arguments(argv)
    source = SOURCES[args.source]

    driver = create_driver(args)
    try:
        if args.initial_login:
            driver.get(source.url)
            input(f"Log in to {source.url} in the browser window, then press Enter here to save the session...")
            return 0

        path = source.output_path(args.output_dir)
        try:
            count = write_csv(path, source.scrape(driver, args.max_pages))
        except TimeoutException:
            print(
                f"Timed out waiting for the stock table at {driver.current_url}. "
                "Is the profile logged in? Run with --initial-login first.",
                file=sys.stderr,
            )
            return 1
        print(f"Wrote {count} stocks to {path}")
        return 0
    finally:
        driver.quit()


if __name__ == "__main__":
    sys.exit(main())
