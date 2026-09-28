"""Registry of the websites the scraper knows about."""

from stockscraper import musaffa
from stockscraper.sourceinfo import SourceInfo

SOURCES: dict[str, SourceInfo] = {
    "musaffa": SourceInfo(
        name="musaffa",
        url=musaffa.URL,
        comment="Shariah-compliance stock screener; the screener table requires a (free) login.",
        scrape=musaffa.scrape,
    ),
}
