"""Three bounded paired trials; source correctness and retained ACKs gate reporting."""

import json
import math
from pathlib import Path
import statistics
import time
import uuid

import control as kernel


def summarize(rows, event):
    values = sorted(r["duration_ms"] for r in rows if r.get("event") == event)
    if len(values) != 1000:
        raise RuntimeError("Expected exactly 1000 successful timed operations")
    return {
        "count": len(values),
        "operations_per_s": len(values) / (sum(values) / 1000),
        "p50_ms": values[math.ceil(len(values) * 0.5) - 1],
        "p95_ms": values[math.ceil(len(values) * 0.95) - 1],
        "p99_ms": values[math.ceil(len(values) * 0.99) - 1],
        "max_ms": values[-1],
    }


def main():
    base = kernel.native_checks.fixture()
    kernel_fixture = json.loads(
        (kernel.ROOT / "results/kernel-client/fixture.json").read_text()
    )
    if base["stage"] != "ready" or kernel_fixture["stage"] != "ready":
        raise RuntimeError("Both correctness gates must pass first")
    directory = (
        kernel.ROOT / "results/kernel-client" / ("comparison-" + str(time.time_ns()))
    )
    directory.mkdir()
    kernel.audit(directory)
    measurements = []
    for repeat in range(3):
        run = uuid.uuid4().hex
        cell = directory / ("repeat-" + str(repeat + 1))
        cell.mkdir()
        native_case = kernel.ROOT / "results/cases" / run
        native_case.mkdir()
        kernel.save(
            native_case / "case.json",
            {"fixture": base["id"], "run": run, "node": 1, "mode": "client-comparison"},
        )
        trial = kernel.ROOT / "results/kernel-client/trials" / run
        trial.mkdir(parents=True)
        fixture = kernel_fixture | {
            "run": run,
            "files": 1000,
            "payload_bytes": 262144,
            "stage": "initializing",
        }
        kernel.save(trial / "fixture.json", fixture)
        order = ("libcephfs", "kernel") if repeat % 2 == 0 else ("kernel", "libcephfs")
        for backend in order:
            if backend == "libcephfs":
                rows = kernel.native_checks.cpp(
                    1, "seed", run, cell, count=1000, size=262144
                )
                witness = kernel.native_checks.export(1, run)
                kernel.native_checks.verify_export(witness, run, cell)
                read_log = next(cell.glob("fresh-verify-" + run + "-*.log"))
                reads = kernel.native_checks.events(read_log.read_text())
            else:
                output = kernel.command("run", 1, run, "seed", 1000, 262144)
                (cell / "kernel-seed.log").write_text(output.stdout + output.stderr)
                rows = kernel.native_checks.events(output.stdout)
                kernel.audit(cell, fixture)
                reads = kernel.native_checks.events((cell / "guest-2.log").read_text())
                fixture["stage"] = "ready"
                kernel.save(trial / "fixture.json", fixture)
            measurements.append(
                {
                    "repeat": repeat + 1,
                    "backend": backend,
                    "run": run,
                    "order": list(order),
                    "write": summarize(rows, "ack"),
                    "read": summarize(reads, "body_read"),
                }
            )
    kernel.native_checks.audit(directory)
    for path, fixture in kernel.trial_fixtures():
        result = directory / ("verify-" + path.parent.name)
        result.mkdir()
        kernel.audit(result, fixture)
    aggregate = {}
    for backend in ("libcephfs", "kernel"):
        aggregate[backend] = {}
        for operation in ("write", "read"):
            selected = [
                r[operation]["operations_per_s"]
                for r in measurements
                if r["backend"] == backend
            ]
            aggregate[backend][operation + "_operations_per_s"] = {
                "median": statistics.median(selected),
                "min": min(selected),
                "max": max(selected),
            }
    result = {
        "selected_depth": 3,
        "classification": "Bounded single-worker recent-write diagnostic; not saturation/capacity or production-tail qualification",
        "trials": measurements,
        "aggregate": aggregate,
        "files_per_trial": 1000,
        "payload_bytes": 262144,
        "same_bytes_per_pair": True,
        "client_transport": "Messenger v2 secure for both paths",
        "durability": "file fsync, close, rename and whole-client/filesystem sync before ACK",
        "limits": [
            "Only 1000 operations per cell; p99 is an unstable small-sample observation, p99.9 unavailable.",
            "Rates use sums of timed operations, excluding generation, local witness fsync, connection setup and verification orchestration.",
            "Recent-write/read-after-write workload, no explicit cache eviction. Physical cache residency is unverified; a fresh process is not physical cold storage.",
            "SDK publication renames from /staging/<run> to /objects/<run>; kernel publication renames within /<run>. Metadata work differs, so this cannot isolate client-layer overhead.",
            "QEMU emulation, shared physical host and fixed one-worker load limit extrapolation. No global winner claim.",
        ],
    }
    kernel.save(directory / "result.json", result)
    print(
        json.dumps({"passed": True, "evidence": str(directory), "aggregate": aggregate})
    )


if __name__ == "__main__":
    main()
