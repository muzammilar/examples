"""Prometheus metrics for funcmapper.

All metrics live on a ``Metrics`` object bound to a ``CollectorRegistry``. The
``funk`` CLI uses the process-wide default registry (``prometheus_client.REGISTRY``);
tests create their own ``Metrics(CollectorRegistry())`` so they don't share state.
"""

import time
from contextlib import contextmanager
from importlib import metadata

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram, Info
from prometheus_client import start_http_server

NAMESPACE = "funcmapper"
DIST_NAME = "funkpkg"

# the mapped functions take well under a microsecond, so the default buckets
# (which start at 5ms) would put every observation in the first bucket
DURATION_BUCKETS = (
    1e-7, 2.5e-7, 5e-7,
    1e-6, 2.5e-6, 5e-6,
    1e-5, 2.5e-5, 5e-5,
    1e-4, 1e-3, 1e-2, 0.1, 1.0,
)


def package_version() -> str:
    """Version of the installed funkpkg distribution (or 'unknown' when run from source)."""
    try:
        return metadata.version(DIST_NAME)
    except metadata.PackageNotFoundError:
        return "unknown"


class Metrics:
    """The funcmapper metrics, registered on ``registry``."""

    def __init__(self, registry: CollectorRegistry = REGISTRY, functions=(), version: str = None):
        self.registry = registry
        self.calls = Counter("calls", "Mapped function calls.", ["function"],
                             namespace=NAMESPACE, registry=registry)
        self.errors = Counter("errors", "Mapped function calls that raised an exception.",
                              ["function"], namespace=NAMESPACE, registry=registry)
        self.duration = Histogram("call_duration_seconds", "Duration of mapped function calls.",
                                  ["function"], namespace=NAMESPACE, registry=registry,
                                  buckets=DURATION_BUCKETS)
        self.iterations = Counter("loop_iterations", "Completed runs of the main loop.",
                                  namespace=NAMESPACE, registry=registry)
        self.last_run = Gauge("last_run_timestamp_seconds",
                              "Unix time of the last completed run of the main loop.",
                              namespace=NAMESPACE, registry=registry)
        self.build = Info("build", "funcmapper build information.",
                          namespace=NAMESPACE, registry=registry)
        self.build.info({"version": version or package_version()})
        # create the labelled series up front so they are exported (as 0) before the
        # first call/error, which keeps rate() queries non-empty
        for name in functions:
            self.calls.labels(name)
            self.errors.labels(name)
            self.duration.labels(name)

    @contextmanager
    def track_call(self, function: str):
        """Count and time one call of ``function``; count it as an error if it raises."""
        self.calls.labels(function).inc()
        start = time.perf_counter()
        try:
            yield
        except Exception:
            self.errors.labels(function).inc()
            raise
        finally:
            self.duration.labels(function).observe(time.perf_counter() - start)

    def run_completed(self):
        """Record one completed run of the main loop."""
        self.iterations.inc()
        self.last_run.set_to_current_time()

    def serve(self, port: int, addr: str = "0.0.0.0"):
        """Expose the registry on http://addr:port/metrics (in a daemon thread)."""
        return start_http_server(port, addr=addr, registry=self.registry)
