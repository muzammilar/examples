"""Tests the musaffa paging/stop logic with a fake driver (no browser needed)."""

from unittest import mock

import pytest
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from stockscraper import musaffa


def _element(text=""):
    el = mock.Mock()
    el.text = text
    el.is_displayed.return_value = True
    el.is_enabled.return_value = True
    return el


class FakeDriver:
    """Serves a list of pages; clicking the "next" button advances to the following page."""

    def __init__(self, pages, next_button_on_last_page=False):
        self.pages = pages  # list of [(ticker, name), ...]
        self.index = 0
        self.next_button_on_last_page = next_button_on_last_page
        self.visited = []
        self.clicks = 0

    def get(self, url):
        self.visited.append(url)

    def execute_script(self, script):
        pass

    def find_element(self, by, value):
        assert by == By.CSS_SELECTOR
        if value == musaffa.TABLE_BODY:
            return self._body()
        if value == musaffa.NEXT_PAGE:
            last = self.index >= len(self.pages) - 1
            if last and not self.next_button_on_last_page:
                raise NoSuchElementException("no next button")
            button = _element()
            button.click.side_effect = self._click
            return button
        raise NoSuchElementException(value)

    def _click(self):
        self.clicks += 1
        # a real site keeps showing the last page if "next" is clicked on it
        self.index = min(self.index + 1, len(self.pages) - 1)

    def _body(self):
        rows = self.pages[self.index]
        body = mock.Mock()

        def find_elements(by, value):
            if value == musaffa.COMPANY_NAME:
                return [_element(name) for _, name in rows]
            if value == musaffa.COMPANY_TICKER:
                return [_element(ticker) for ticker, _ in rows]
            return []

        body.find_elements.side_effect = find_elements
        return body


PAGES = [
    [("AAPL", "Apple"), ("MSFT", "Microsoft")],
    [("GOOG", "Alphabet")],
    [("NVDA", "Nvidia"), ("AMZN", "Amazon")],
]


@pytest.fixture(autouse=True)
def fast_waits(monkeypatch):
    # a missing "next" button makes WebDriverWait time out; keep that (and the page-change delay) short
    monkeypatch.setattr(musaffa, "PAGE_LOAD_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(musaffa, "PAGE_CHANGE_DELAY_SECONDS", 0)


def test_scrapes_all_pages_until_no_next_button():
    driver = FakeDriver(PAGES)
    stocks = list(musaffa.scrape(driver))
    assert [(s.symbol, s.name, s.page_number) for s in stocks] == [
        ("AAPL", "Apple", 1),
        ("MSFT", "Microsoft", 1),
        ("GOOG", "Alphabet", 2),
        ("NVDA", "Nvidia", 3),
        ("AMZN", "Amazon", 3),
    ]
    assert driver.visited == [musaffa.URL]
    assert driver.clicks == 2


def test_max_pages_stops_early():
    driver = FakeDriver(PAGES)
    stocks = list(musaffa.scrape(driver, max_pages=2))
    assert {s.page_number for s in stocks} == {1, 2}
    assert driver.clicks == 1


def test_stops_when_page_does_not_change():
    # the "next" button stays clickable on the last page, but the table no longer changes
    driver = FakeDriver(PAGES, next_button_on_last_page=True)
    stocks = list(musaffa.scrape(driver))
    assert len(stocks) == 5
    assert driver.clicks == 3


def test_empty_table_yields_nothing():
    driver = FakeDriver([[]])
    assert list(musaffa.scrape(driver)) == []
    assert driver.clicks == 0
