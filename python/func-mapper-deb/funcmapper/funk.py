"""Entry point of the `funk` console script (and the systemd service)."""

import argparse
import sys
import time

from funcmapper import logger as funklogger
from funcmapper import maps as funkmap
from funcmapper import metrics as funkmetrics

LOGGER_NAME = "funcmapper"

# (function name, positional args, keyword args)
MAPPERS = [
    ("rails", ("inter-galactic", "62 inches"), {}),
    ("cylinders", ("hydraulic", "3 feet"), {"diameter": "4 inches"}),
    ("oranges", (), {"count": "12", "origin": "valencia"}),
]

_default_metrics = None


def default_metrics() -> funkmetrics.Metrics:
    """The process-wide metrics on the default prometheus_client registry (created once)."""
    global _default_metrics
    if _default_metrics is None:
        _default_metrics = funkmetrics.Metrics(functions=sorted(funkmap.maps))
    return _default_metrics


def run(mappers=None, metrics: funkmetrics.Metrics = None) -> list:
    """Call every mapped function once and return the results.

    With ``metrics``, every call is counted and timed, and failures are counted as errors
    (the exception is still raised).
    """
    results = []
    for name, args, kwargs in (mappers or MAPPERS):
        if metrics is None:
            results.append(funkmap.call(name, *args, **kwargs))
            continue
        with metrics.track_call(name):
            results.append(funkmap.call(name, *args, **kwargs))
    return results


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="funk", description="Call a set of mapped functions.")
    parser.add_argument("--interval", type=float, default=0,
                        help="seconds between runs; 0 (default) runs once and exits")
    parser.add_argument("--log-file", default=None,
                        help="also write results to this log file")
    parser.add_argument("--log-level", default="info",
                        choices=["debug", "info", "warning", "error"])
    parser.add_argument("--metrics-port", type=int, default=0,
                        help="serve Prometheus metrics on this port at /metrics; 0 (default) disables it")
    args = parser.parse_args(argv)
    if not 0 <= args.metrics_port <= 65535:
        parser.error("--metrics-port must be between 0 and 65535")
    return args


def main(argv=None, metrics: funkmetrics.Metrics = None) -> int:
    args = parse_args(argv)
    # results always go to stdout (journald under systemd); optionally also to a log file
    if args.log_file:
        funklogger.configure_file_logging(LOGGER_NAME, args.log_level, args.log_file)
    log = funklogger.get_logger(LOGGER_NAME)

    metrics = metrics or default_metrics()
    if args.metrics_port:
        metrics.serve(args.metrics_port)
        print(f"serving metrics on :{args.metrics_port}/metrics", file=sys.stderr, flush=True)

    while True:
        for result in run(metrics=metrics):
            print(result, flush=True)
            log.info(result)
        metrics.run_completed()
        if args.interval <= 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
