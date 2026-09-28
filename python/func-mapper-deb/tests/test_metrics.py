"""Tests for the Prometheus metrics (each test uses its own CollectorRegistry)."""

import socket
import time
import urllib.request

import pytest
from prometheus_client import CollectorRegistry

from funcmapper import funk, maps, metrics

pytestmark = pytest.mark.unit

NAMES = sorted(maps.maps)


@pytest.fixture
def m():
    return metrics.Metrics(CollectorRegistry(), functions=NAMES, version="1.2.3-4")


def value(m, name, **labels):
    return m.registry.get_sample_value(name, labels or None)


def test_series_exist_before_any_call(m):
    for name in NAMES:
        assert value(m, "funcmapper_calls_total", function=name) == 0
        assert value(m, "funcmapper_errors_total", function=name) == 0
        assert value(m, "funcmapper_call_duration_seconds_count", function=name) == 0
    assert value(m, "funcmapper_loop_iterations_total") == 0
    assert value(m, "funcmapper_last_run_timestamp_seconds") == 0


def test_build_info(m):
    assert value(m, "funcmapper_build_info", version="1.2.3-4") == 1


def test_default_version_is_a_string():
    assert isinstance(metrics.package_version(), str)


def test_run_counts_and_times_calls(m):
    funk.run(metrics=m)
    funk.run(metrics=m)
    for name, _, _ in funk.MAPPERS:
        assert value(m, "funcmapper_calls_total", function=name) == 2
        assert value(m, "funcmapper_call_duration_seconds_count", function=name) == 2
        assert value(m, "funcmapper_call_duration_seconds_sum", function=name) > 0
        assert value(m, "funcmapper_errors_total", function=name) == 0


def test_errors_are_counted_and_reraised(m):
    with pytest.raises(TypeError):
        funk.run([("rails", ("only-one-arg",), {})], metrics=m)
    with pytest.raises(KeyError):
        funk.run([("bananas", (), {})], metrics=m)
    assert value(m, "funcmapper_calls_total", function="rails") == 1
    assert value(m, "funcmapper_errors_total", function="rails") == 1
    assert value(m, "funcmapper_errors_total", function="bananas") == 1
    assert value(m, "funcmapper_call_duration_seconds_count", function="rails") == 1


def test_run_without_metrics_is_uninstrumented(m):
    assert funk.run() == funk.run(metrics=m)
    assert value(m, "funcmapper_calls_total", function="rails") == 1


def test_main_records_loop_iterations(m, monkeypatch, capsys):
    class Stop(Exception):
        pass

    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise Stop

    monkeypatch.setattr(funk.time, "sleep", fake_sleep)
    before = time.time()
    with pytest.raises(Stop):
        funk.main(["--interval", "1"], metrics=m)
    assert value(m, "funcmapper_loop_iterations_total") == 3
    assert value(m, "funcmapper_calls_total", function="oranges") == 3
    assert before <= value(m, "funcmapper_last_run_timestamp_seconds") <= time.time()


def test_main_once_records_one_iteration(m, capsys):
    assert funk.main([], metrics=m) == 0
    assert value(m, "funcmapper_loop_iterations_total") == 1


def test_metrics_port_flag():
    assert funk.parse_args([]).metrics_port == 0
    assert funk.parse_args(["--metrics-port", "9118"]).metrics_port == 9118
    for bad in (["--metrics-port", "-1"], ["--metrics-port", "70000"], ["--metrics-port", "x"]):
        with pytest.raises(SystemExit):
            funk.parse_args(bad)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_main_serves_metrics_over_http(m, capsys):
    port = free_port()
    assert funk.main(["--metrics-port", str(port)], metrics=m) == 0
    body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
    assert 'funcmapper_calls_total{function="rails"} 1.0' in body
    assert "funcmapper_loop_iterations_total 1.0" in body
    assert 'funcmapper_build_info{version="1.2.3-4"} 1.0' in body
    assert f":{port}/metrics" in capsys.readouterr().err
