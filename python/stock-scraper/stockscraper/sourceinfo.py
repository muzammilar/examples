"""Data types describing a scrape source and the stocks scraped from it."""

import csv
import dataclasses
import os
from collections.abc import Callable, Iterable

from selenium.webdriver.remote.webdriver import WebDriver


@dataclasses.dataclass(frozen=True)
class StockInfo:
    """A single stock listed on a source website."""

    symbol: str
    name: str
    page_number: int


@dataclasses.dataclass(frozen=True)
class SourceInfo:
    """A website to scrape stock tickers from.

    Attributes:
        name: Short identifier, also used as the output file name.
        url: Page to open (for both login and scraping).
        comment: Human-readable description.
        scrape: Function that takes a driver and a max page count (0 = all) and yields stocks.
    """

    name: str
    url: str
    comment: str
    scrape: Callable[[WebDriver, int], Iterable[StockInfo]]

    def output_path(self, output_dir: str) -> str:
        """Returns the CSV path for this source inside `output_dir`."""
        return os.path.join(output_dir, f"{self.name}.csv")


def write_csv(path: str, stocks: Iterable[StockInfo]) -> int:
    """Writes stocks to a CSV file as they are scraped, returning the number of rows written."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fields = [f.name for f in dataclasses.fields(StockInfo)]
    count = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for stock in stocks:
            writer.writerow(dataclasses.asdict(stock))
            count += 1
    return count
