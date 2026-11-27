"""Bounded paired client-path comparison on this example's shared backend."""

import json
import math
import os
from pathlib import Path
import statistics
import time
import uuid

import http_project as http


def events(output):
    return [json.loads(line) for line in output.splitlines()]


def summarize(rows, event, count):
    values = sorted(row["duration_ms"] for row in rows if row.get("event") == event)
    if len(values) != count:
        raise RuntimeError(f"Expected exactly {count} successful timed operations")
    return {
        "count": len(values),
        "operations_per_s": len(values) / (sum(values) / 1000),
        "p50_ms": values[math.ceil(len(values) * 0.5) - 1],
        "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
        "p99_ms": values[math.ceil(len(values) * 0.99) - 1],
        "max_ms": values[-1],
    }


def main():
    count = int(os.environ.get("COMPARE_FILES", "256"))
    if not 1 <= count <= 1024:
        raise ValueError("COMPARE_FILES must be between 1 and 1024")
    parent_path = http.ROOT / "results/fixture.json"
    api_path = http.ROOT / "results/native-http/fixture.json"
    parent = json.loads(parent_path.read_text())
    api = json.loads(api_path.read_text())
    if parent["stage"] != "ready" or api["stage"] != "ready":
        raise RuntimeError("Both paths require a passing correctness baseline")
    directory = (
        http.ROOT / "results/native-http" / ("comparison-" + str(time.time_ns()))
    )
    directory.mkdir()
    http.lab.save(directory / "status.json", {"passed": False})
    http.lab.audit(directory)
    measurements = []
    for repeat in range(3):
        run = uuid.uuid4().hex
        cell = directory / ("repeat-" + str(repeat + 1))
        cell.mkdir()
        order = ("fuse", "http") if repeat % 2 == 0 else ("http", "fuse")
        for backend in order:
            if backend == "fuse":
                parent["runs"].append(run)
                http.lab.save(parent_path, parent)
                output = http.lab.execute("writer", "seed", run, count, 262144)
                (cell / "fuse-seed.log").write_text(output)
                rows = events(output)
                output = http.lab.execute("reader", "verify", run)
                (cell / "fuse-read.log").write_text(output)
                read_rows = events(output)
            else:
                api["runs"].append(run)
                api["run_counts"][run] = count
                http.lab.save(api_path, api)
                output = http.execute("seed", run, count, 262144)
                (cell / "http-seed.log").write_text(output)
                rows = events(output)
                output = http.execute("verify", run, "http://filer1:8888")
                (cell / "http-read.log").write_text(output)
                read_rows = events(output)
            measurements.append(
                {
                    "repeat": repeat + 1,
                    "backend": backend,
                    "run": run,
                    "order": list(order),
                    "write": summarize(rows, "ack", count),
                    "read": summarize(
                        read_rows, "body_read" if backend == "fuse" else "read", count
                    ),
                }
            )
    verified = http.lab.audit(directory)
    aggregate = {}
    for backend in ("fuse", "http"):
        aggregate[backend] = {}
        for operation in ("write", "read"):
            values = [
                r[operation]["operations_per_s"]
                for r in measurements
                if r["backend"] == backend
            ]
            aggregate[backend][operation + "_operations_per_s"] = {
                "median": statistics.median(values),
                "min": min(values),
                "max": max(values),
            }
    result = {
        "selected_depth": 3,
        "files_per_trial": count,
        "payload_bytes": 262144,
        "trials": measurements,
        "aggregate": aggregate,
        "verified": verified,
        "classification": "Short single-worker warm/read-after-write comparison, not saturation or production-tail qualification",
        "same_bytes_per_pair": True,
        "limits": [
            "FUSE performs staging/file fsync/rename/directory fsync; HTTP uses a size-checked PUT receipt with fsync=true. Publication semantics differ.",
            "Native HTTP payloads pass through the filer; FUSE uses metadata RPCs and direct volume I/O. This is a complete access-path comparison, not an isolated FUSE-overhead measurement.",
            "Both FUSE mounts remain running during all trials; idle watch/cache costs are common co-tenancy.",
            f"Only {count} operations per cell; p99 is a small-sample observation and p99.9 unavailable.",
            "Rates exclude generation, external-witness fsync, connection setup and orchestration. Recent writes with no explicit cache eviction; physical cache residency is unverified. Fresh processes do not establish physical cold-read performance.",
        ],
    }
    http.lab.save(directory / "result.json", result)
    http.lab.save(directory / "status.json", {"passed": True})
    print(
        json.dumps({"passed": True, "evidence": str(directory), "aggregate": aggregate})
    )


if __name__ == "__main__":
    main()
