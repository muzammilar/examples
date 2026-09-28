"""Scrapes the stock screener table on musaffa.com (requires a logged-in session)."""

import time
from collections.abc import Iterator

from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from stockscraper.sourceinfo import StockInfo

URL = "https://screener.musaffa.com/cabinet/onboarding"

# CSS selectors for the screener table
TABLE_BODY = ".table--body"
COMPANY_NAME = ".mb-0.company--name"
COMPANY_TICKER = ".mb-0.stock--name"
NEXT_PAGE = ".bi.bi-chevron-right"

PAGE_LOAD_TIMEOUT_SECONDS = 20
# delay after clicking "next" so the table has time to re-render
PAGE_CHANGE_DELAY_SECONDS = 3.5


def scrape(driver: WebDriver, max_pages: int = 0) -> Iterator[StockInfo]:
    """Yields every stock in the screener table, page by page.

    Args:
        driver: A web driver whose profile is already logged in to musaffa.com.
        max_pages: Stop after this many pages; 0 scrapes until there is no next page.

    Yields:
        One `StockInfo` per table row.
    """
    driver.get(URL)
    page_number = 1
    previous_first_ticker = None
    while True:
        body = WebDriverWait(driver, PAGE_LOAD_TIMEOUT_SECONDS).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, TABLE_BODY)),
        )
        names = [e.text for e in body.find_elements(By.CSS_SELECTOR, COMPANY_NAME)]
        tickers = [e.text for e in body.find_elements(By.CSS_SELECTOR, COMPANY_TICKER)]
        if not tickers or tickers[0] == previous_first_ticker:
            # empty table, or the page did not change after clicking "next"
            break
        previous_first_ticker = tickers[0]

        for name, ticker in zip(names, tickers, strict=False):
            yield StockInfo(symbol=ticker, name=name, page_number=page_number)
        print(f"musaffa: scraped page {page_number} ({len(tickers)} rows)")

        if max_pages and page_number >= max_pages:
            break
        if not _go_to_next_page(driver):
            break
        page_number += 1


def _go_to_next_page(driver: WebDriver) -> bool:
    """Clicks the pagination "next" button, returning False if there is none."""
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    try:
        button = WebDriverWait(driver, PAGE_LOAD_TIMEOUT_SECONDS).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, NEXT_PAGE)),
        )
    except (NoSuchElementException, TimeoutException):
        return False
    button.click()
    time.sleep(PAGE_CHANGE_DELAY_SECONDS)
    return True
