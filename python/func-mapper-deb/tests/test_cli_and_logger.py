"""Tests for the funk CLI, maps dispatch and logger setup"""

import logging
import logging.handlers

import pytest

from funcmapper import funk, logger, maps

pytestmark = pytest.mark.unit


# --- CLI argument parsing ---------------------------------------------------

def test_parse_args_defaults():
    args = funk.parse_args([])
    assert args.interval == 0
    assert args.log_file is None
    assert args.log_level == "info"


def test_parse_args_values():
    args = funk.parse_args(["--interval", "2.5", "--log-file", "/tmp/x.log", "--log-level", "debug"])
    assert args.interval == 2.5
    assert args.log_file == "/tmp/x.log"
    assert args.log_level == "debug"


@pytest.mark.parametrize("argv", [["--log-level", "verbose"], ["--interval", "abc"], ["--unknown"]])
def test_parse_args_invalid(argv):
    with pytest.raises(SystemExit):
        funk.parse_args(argv)


# --- main loop --------------------------------------------------------------

def test_interval_zero_runs_once(monkeypatch, capsys):
    def no_sleep(_):
        raise AssertionError("sleep must not be called with --interval 0")
    monkeypatch.setattr(funk.time, "sleep", no_sleep)
    assert funk.main(["--interval", "0"]) == 0
    assert len(capsys.readouterr().out.splitlines()) == len(funk.MAPPERS)


def test_positive_interval_loops_and_sleeps(monkeypatch, capsys):
    sleeps = []

    class Stop(Exception):
        pass

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise Stop

    monkeypatch.setattr(funk.time, "sleep", fake_sleep)
    with pytest.raises(Stop):
        funk.main(["--interval", "5"])
    assert sleeps == [5, 5]
    # 2 sleeps -> 2 full runs completed before the second sleep
    assert len(capsys.readouterr().out.splitlines()) == 2 * len(funk.MAPPERS)


def test_log_file_is_written(tmp_path):
    logfile = tmp_path / "funk.log"
    assert funk.main(["--log-file", str(logfile), "--log-level", "info"]) == 0
    lines = logfile.read_text().splitlines()
    assert len(lines) == len(funk.MAPPERS)
    assert all(" INFO - " in line for line in lines)


def test_log_level_filters_file(tmp_path):
    logfile = tmp_path / "funk.log"
    assert funk.main(["--log-file", str(logfile), "--log-level", "warning"]) == 0
    assert logfile.read_text() == ""


def test_run_custom_mappers():
    assert funk.run([("rails", ("x", "1m"), {})]) == ["rails: 'x' with length 1m"]


# --- maps.call ----------------------------------------------------------------

def test_maps_call_unknown_name_message():
    with pytest.raises(KeyError) as excinfo:
        maps.call("bananas")
    message = str(excinfo.value)
    assert "bananas" in message
    for name in maps.maps:
        assert name in message


def test_maps_call_passes_kwargs():
    assert maps.call("cylinders", "c", "2m", diameter="1cm").endswith("diameter 1cm")
    assert maps.call("oranges", count="3") == "oranges: count=3"


def test_maps_call_propagates_type_errors():
    with pytest.raises(TypeError):
        maps.call("rails", "only-one-arg")


# --- logger -------------------------------------------------------------------

def test_get_logger_returns_same_instance():
    assert logger.get_logger("same-name") is logger.get_logger("same-name")


def test_configure_file_logging_handler_and_level(tmp_path):
    log = logger.configure_file_logging("test-level", "warning", str(tmp_path / "l.log"))
    assert log.level == logging.WARNING
    (handler,) = log.handlers
    assert isinstance(handler, logging.handlers.WatchedFileHandler)
    assert handler.baseFilename == str(tmp_path / "l.log")


def test_configure_file_logging_switches_file(tmp_path):
    first, second = tmp_path / "a.log", tmp_path / "b.log"
    logger.configure_file_logging("test-switch", "info", str(first)).info("one")
    logger.configure_file_logging("test-switch", "info", str(second)).info("two")
    assert "one" in first.read_text() and "two" not in first.read_text()
    assert "two" in second.read_text()
