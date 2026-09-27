"""Test Function Mapping"""

import pytest

from funcmapper import functions, funk, logger, maps

# Mark every test in this file as a unit test
pytestmark = pytest.mark.unit


def test_functions():
    assert functions.rails("a", "1m") == "rails: 'a' with length 1m"
    assert functions.cylinders("c", "2m") == "cylinders: 'c' with length 2m"
    assert functions.cylinders("c", "2m", diameter="3cm") == "cylinders: 'c' with length 2m and diameter 3cm"
    assert functions.oranges() == "oranges: none"
    assert functions.oranges(origin="x", count="2") == "oranges: count=2, origin=x"


def test_maps_call():
    assert maps.call("rails", "a", "1m") == functions.rails("a", "1m")
    with pytest.raises(KeyError):
        maps.call("bananas")


def test_run_default_mappers():
    results = funk.run()
    assert len(results) == len(funk.MAPPERS)
    assert results[0].startswith("rails:")


def test_main_once(capsys):
    assert funk.main([]) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == len(funk.MAPPERS)


def test_main_log_file(tmp_path):
    logfile = tmp_path / "funk.log"
    assert funk.main(["--log-file", str(logfile)]) == 0
    assert "rails:" in logfile.read_text()


def test_configure_file_logging_replaces_handlers(tmp_path):
    name = "test-funcmapper"
    logger.configure_file_logging(name, "info", str(tmp_path / "a.log"))
    log = logger.configure_file_logging(name, "info", str(tmp_path / "b.log"))
    assert len(log.handlers) == 1
