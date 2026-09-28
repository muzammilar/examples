from stockscraper import musaffa
from stockscraper.sources import SOURCES


def test_registry_keys_match_source_names():
    assert SOURCES
    for key, source in SOURCES.items():
        assert key == source.name
        assert source.url.startswith("https://")
        assert callable(source.scrape)


def test_musaffa_is_registered():
    assert SOURCES["musaffa"].url == musaffa.URL
    assert SOURCES["musaffa"].scrape is musaffa.scrape
