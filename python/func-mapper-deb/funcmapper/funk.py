"""Entry point of the `funk` console script (and the systemd service)."""

import argparse
import sys
import time

from funcmapper import logger as funklogger
from funcmapper import maps as funkmap

LOGGER_NAME = "funcmapper"

# (function name, positional args, keyword args)
MAPPERS = [
    ("rails", ("inter-galactic", "62 inches"), {}),
    ("cylinders", ("hydraulic", "3 feet"), {"diameter": "4 inches"}),
    ("oranges", (), {"count": "12", "origin": "valencia"}),
]


def run(mappers=None) -> list:
    """Call every mapped function once and return the results."""
    return [funkmap.call(name, *args, **kwargs) for name, args, kwargs in (mappers or MAPPERS)]


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="funk", description="Call a set of mapped functions.")
    parser.add_argument("--interval", type=float, default=0,
                        help="seconds between runs; 0 (default) runs once and exits")
    parser.add_argument("--log-file", default=None,
                        help="also write results to this log file")
    parser.add_argument("--log-level", default="info",
                        choices=["debug", "info", "warning", "error"])
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # results always go to stdout (journald under systemd); optionally also to a log file
    if args.log_file:
        funklogger.configure_file_logging(LOGGER_NAME, args.log_level, args.log_file)
    log = funklogger.get_logger(LOGGER_NAME)

    while True:
        for result in run():
            print(result, flush=True)
            log.info(result)
        if args.interval <= 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
