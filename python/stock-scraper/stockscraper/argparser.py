"""Command-line argument parsing for the stock scraper."""

import argparse

from stockscraper.sources import SOURCES


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments.

    Args:
        argv: Arguments to parse. Defaults to `sys.argv[1:]`.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Scrape stock tickers from a website using Selenium and a persistent browser profile.",
    )
    parser.add_argument(
        "--source",
        choices=sorted(SOURCES),
        default="musaffa",
        help="Website to scrape (default: %(default)s).",
    )
    parser.add_argument(
        "--browser",
        choices=["chrome", "firefox"],
        default="chrome",
        help="Browser to drive (default: %(default)s).",
    )
    parser.add_argument(
        "--initial-login",
        action="store_true",
        help="Open the source website and wait for you to log in manually. The session is saved in the browser profile.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run the browser without a window (only useful once the profile is already logged in).",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Maximum number of pages to scrape; 0 means all pages (default: %(default)s).",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Directory to write the CSV output into (default: %(default)s).",
    )
    parser.add_argument(
        "--chrome-user-data-dir",
        default="_userdatachrome",
        help="Path to the Chrome user data directory (default: %(default)s).",
    )
    parser.add_argument(
        "--chrome-profile-directory",
        default="Default",
        help="Name of the profile directory inside the Chrome user data directory (default: %(default)s).",
    )
    parser.add_argument(
        "--firefox-profile",
        default="_userdatafirefox",
        help="Path to the Firefox profile directory (default: %(default)s).",
    )
    return parser.parse_args(argv)
