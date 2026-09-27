"""Benchmarks for the mapped functions and the maps.call dispatch overhead.

Run with `make bench` (requires pytest-benchmark from test_requirements.txt).
"""

import pytest

from funcmapper import functions, maps

pytestmark = pytest.mark.benchmark

CASES = {
    "rails": (("inter-galactic", "62 inches"), {}),
    "cylinders": (("hydraulic", "3 feet"), {"diameter": "4 inches"}),
    "oranges": ((), {"count": "12", "origin": "valencia"}),
}


@pytest.mark.parametrize("name", sorted(CASES))
def test_direct_call(benchmark, name):
    args, kwargs = CASES[name]
    func = getattr(functions, name)
    benchmark.group = name
    result = benchmark(func, *args, **kwargs)
    assert result.startswith(name)


@pytest.mark.parametrize("name", sorted(CASES))
def test_maps_call(benchmark, name):
    args, kwargs = CASES[name]
    benchmark.group = name
    result = benchmark(maps.call, name, *args, **kwargs)
    assert result.startswith(name)
