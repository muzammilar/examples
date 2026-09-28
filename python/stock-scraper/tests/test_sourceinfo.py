import csv

from stockscraper.sourceinfo import SourceInfo, StockInfo, write_csv


def test_write_csv_creates_directory_and_writes_rows(tmp_path):
    path = tmp_path / "nested" / "out.csv"
    stocks = [StockInfo("AAPL", "Apple Inc.", 1), StockInfo("MSFT", "Microsoft, Corp.", 2)]

    assert write_csv(str(path), iter(stocks)) == 2

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows == [
        {"symbol": "AAPL", "name": "Apple Inc.", "page_number": "1"},
        {"symbol": "MSFT", "name": "Microsoft, Corp.", "page_number": "2"},
    ]


def test_write_csv_empty_writes_header_only(tmp_path):
    path = tmp_path / "empty.csv"
    assert write_csv(str(path), []) == 0
    assert path.read_text(encoding="utf-8").strip() == "symbol,name,page_number"


def test_output_path():
    source = SourceInfo(name="x", url="https://example.com", comment="", scrape=lambda d, n: [])
    assert source.output_path("data") == "data/x.csv"
