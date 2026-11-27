"""Three small fresh-process query trials; latency diagnostics, not saturation."""

import json
import statistics
import time
import uuid

import lab


def main():
    path = lab.directory("benchmark")
    lab.workload("verify", path)
    fixture = lab.fixture()
    trials = []
    for repeat in range(3):
        order = "checkpoint-first" if repeat % 2 == 0 else "async-first"
        started = time.monotonic()
        output = lab.command(
            "exec",
            "-T",
            "-e",
            "RUN_ID=" + fixture["id"],
            "-e",
            "CASE_ID=" + uuid.uuid4().hex,
            "-e",
            "READ_ORDER=" + order,
            "orders",
            "/orders",
            "query",
            timeout=180,
        )
        (path / ("trial-" + str(repeat + 1) + ".log")).write_text(output)
        rows = [json.loads(line) for line in output.splitlines()]
        for table in ("checkpointed", "asynchronous"):
            values = sorted(
                row["duration_ms"]
                for row in rows
                if row.get("event") == "read"
                and row["table"] == table
                and not row["warmup"]
            )
            if len(values) != 100:
                raise RuntimeError("Expected 100 exact measured queries per sink")
            trials.append(
                {
                    "repeat": repeat + 1,
                    "table": table,
                    "order": order,
                    "queries": 100,
                    "rows_per_query": 64,
                    "warmup_queries": 10,
                    "mean_ms": statistics.mean(values),
                    "p50_ms": values[49],
                    "p95_ms": values[94],
                    "p99_ms": values[98],
                    "max_ms": values[-1],
                    "whole_trial_wall_s": time.monotonic() - started,
                }
            )
    result = {
        "performance_depth": 3,
        "classification": "Short hot historical-row query diagnostics; no saturation/capacity winner",
        "trials": trials,
        "limits": [
            "Both tables share one backend and both sink paths remain active/idle. This does not compare independent databases.",
            "Small repeated 64-row working set, ten discarded warmup queries, no OS cache eviction or physical cold-read claim.",
            "Only 100 measured queries per cell; p99 is a small-sample observation, p99.9 unavailable.",
            "Queries use one synchronous worker with 200ms think time per pair; wall rates are offered-load-limited, not capacity.",
        ],
    }
    lab.save(path / "result.json", result)
    lab.save(path / "status.json", {"passed": True})
    print(json.dumps({"passed": True, "evidence": str(path)}))


if __name__ == "__main__":
    main()
