"""Same fixed analytical input, alone and alongside independently witnessed appends."""

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import statistics
import subprocess
import time
import uuid

import control
from resources import Recorder, summarize_records


def bounded_setting(name, default, low, high):
    value = int(os.environ.get(name, default))
    if not low <= value <= high:
        raise ValueError(f"{name} must be in {low}..{high}")
    return value


def host_rss_bytes(value, system=None):
    return value * (1 if (system or os.uname().sysname) == "Darwin" else 1024)


def query_summary(records):
    queries = [
        r
        for r in records
        if r.get("kind") == "analytic-query" and r["phase"] == "measurement"
    ]
    if not queries or any(not r["passed"] for r in queries):
        raise RuntimeError("missing or failed analytical output verification")
    elapsed = sorted(r["query_fetch_ms"] for r in queries)
    start = next(
        r["started_ns"]
        for r in records
        if r.get("kind") == "phase-start" and r["phase"] == "measurement"
    )
    ending = next(
        r
        for r in records
        if r.get("kind") == "phase-end" and r["phase"] == "measurement"
    )
    seconds = ending.get("elapsed_ns", ending["ended_ns"] - start) / 1e9
    if seconds <= 0:
        raise RuntimeError("measurement duration must be positive")
    summary = {
        "queries": len(queries),
        "measured_seconds": seconds,
        "queries_per_second": len(queries) / seconds,
        "mean_ms": statistics.mean(elapsed),
        "p50_ms": statistics.median(elapsed),
        "p95_ms": elapsed[math.ceil(0.95 * len(elapsed)) - 1],
        "p99_ms": elapsed[math.ceil(0.99 * len(elapsed)) - 1],
        "validation_mean_ms": statistics.mean(r["validation_ms"] for r in queries),
        "timed_scope": "SQL request through complete fetching/decoding; group validation reported separately. Measurement includes the final query completing after the deadline.",
    }
    if "elapsed_ns" in ending:
        summary["phase_timing_clock"] = "monotonic"
    return summary


def cell(backend, concurrent, trial, warmup, duration):
    case = control.LiveCase(backend, "htap-live" if concurrent else "htap-alone")
    root = case.root
    process = None
    identity = None
    name = "cedar-analytics-" + case.identity[:12]
    try:
        original = control.container(backend)
        if (
            original["HostConfig"]["NanoCpus"] != 4000000000
            or original["HostConfig"]["Memory"] != 6 * 1024**3
        ):
            raise RuntimeError("database budget differs from the matched comparison")
        control.compose("stop", backend)
        stopped = control.container(backend)
        control.save(root / "fresh-process-stopped.json", stopped)
        if stopped["State"]["Running"] or stopped["State"]["ExitCode"] != 0:
            raise RuntimeError("fresh-process trial requires a clean database stop")
        control.compose("up", "--detach", "--wait", backend)
        if (
            control.container(backend)["State"]["StartedAt"]
            == original["State"]["StartedAt"]
        ):
            raise RuntimeError("database process did not restart for this trial")
        before = control.native_snapshot(backend)
        if concurrent:
            case.start()
            case.progress()
        endpoint = (
            os.environ.get("DOCKER_HOST")
            or json.loads(control.run(["docker", "context", "inspect"]))[0][
                "Endpoints"
            ]["docker"]["Host"]
        )
        targets = [(backend, control.container(backend)["Id"])] + list(
            case.actors.items()
        )
        with Recorder(
            root / "analytics-resources.jsonl", endpoint, targets, control.sample_disk
        ) as recorder:
            with (
                (root / "analytics.log").open("w") as output,
                (root / "analytics.stderr").open("w") as errors,
                (root / "analytics.log").open() as phase_events,
            ):
                process = subprocess.Popen(
                    [
                        "docker",
                        "compose",
                        "run",
                        "--no-deps",
                        "--name",
                        name,
                        "-T",
                        "orders-workload",
                        "analytics",
                    ],
                    cwd=control.ROOT,
                    env=control.ENV
                    | {
                        "CEDAR_LAB_DB_HOST": backend,
                        "CEDAR_LAB_ENGINE": backend,
                        "ANALYTICS_WARMUP_S": str(warmup),
                        "ANALYTICS_DURATION_S": str(duration),
                    },
                    stdout=output,
                    stderr=errors,
                )
                deadline = time.monotonic() + warmup + duration + 90
                while process.poll() is None:
                    if concurrent:
                        case.assert_running()
                    if time.monotonic() > deadline:
                        raise TimeoutError(
                            "analytical client exceeded bounded cell duration"
                        )
                    if identity is None:
                        ids = control.run(
                            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"]
                        ).split()
                        if ids:
                            identity = control.inspect(ids[0])["Id"]
                            recorder.targets.append(("analytics", identity))
                    # Save phase labels from actual client events, not launch-time guesses.
                    while True:
                        position = phase_events.tell()
                        line = phase_events.readline()
                        if not line.endswith("\n"):
                            phase_events.seek(position)
                            break
                        record = json.loads(line)
                        if record.get("kind") == "phase-start":
                            recorder.phase = record["phase"]
                    time.sleep(1)
                if process.returncode:
                    raise RuntimeError(
                        f"analytical client exited {process.returncode}: see {root}/analytics.stderr"
                    )
            if identity is None:
                identity = control.inspect(name)["Id"]
            item = control.inspect(identity)
            control.save(root / "analytics-inspect.json", item)
            if item["State"]["Running"] or item["State"]["ExitCode"] != 0:
                raise RuntimeError("native analytical container failed")
            if (
                item["HostConfig"]["NanoCpus"] != 1000000000
                or item["HostConfig"]["Memory"] != 512 * 1024**2
            ):
                raise RuntimeError("analytical workload budget differs")
            control.run(["docker", "rm", identity])
            identity = None
            case.close()
            recorder.phase = "final-verification"
            verification = control.reconcile(backend)
            native_after = control.native_snapshot(backend)
        records = [
            json.loads(line)
            for line in (root / "analytics.log").read_text().splitlines()
        ]
        summary = query_summary(records)
        measurements = [
            r
            for r in records
            if r.get("kind") == "analytic-query" and r["phase"] == "measurement"
        ]
        start, end = measurements[0]["started_ns"], measurements[-1]["ended_ns"]
        phase = {}
        for role in case.actors:
            events = control.journal(root / (role + ".jsonl"))
            phase[role] = control.phase_summary(events, start, end)
            attempted = sum(v["count"] for v in phase[role].values())
            if (
                phase[role]["ok"]["count"] < 5
                or phase[role]["ok"]["count"] / max(attempted, 1) < 0.95
            ):
                raise RuntimeError(
                    "live workload did not sustain the healthy-cell success contract"
                )
        samples = [
            json.loads(line)
            for line in (root / "analytics-resources.jsonl").read_text().splitlines()
        ]
        verification = {
            k: v
            for k, v in verification.items()
            if k not in {"observed_live_ids", "export_process"}
        }
        result = {
            "passed": True,
            "backend": backend,
            "concurrent": concurrent,
            "trial": trial,
            "history": case.identity,
            "query": summary,
            "live_workload": phase,
            "verification": verification,
            "native_before": before,
            "native_after": native_after,
            "process_resources": [
                r for r in records if r.get("kind") == "process-resources"
            ],
            "measurement_resources": summarize_records(
                [
                    s
                    for s in samples
                    if start <= s["started_ns"] and s["ended_ns"] <= end
                ]
            ),
            "all_resources": summarize_records(samples),
            "workload_image": item["Image"],
            "warmup_s": warmup,
            "duration_s": duration,
        }
        control.save(root / "analytics-result.json", result)
        return result
    except BaseException as error:
        control.save(
            root / "failure.json",
            {"passed": False, "error": str(error), "stage": "htap"},
        )
        raise
    finally:
        # Stop only this exact, label-verified client before joining its Docker CLI.
        ids = control.run(
            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"]
        ).split()
        for candidate in ids:
            item = control.inspect(candidate)
            if item["State"]["Running"]:
                control.run(["docker", "stop", "--time", "10", candidate])
            control.save(
                root / "analytics-final-inspect.json", control.inspect(candidate)
            )
            control.run(["docker", "rm", candidate])
        if process is not None and process.poll() is None:
            process.wait(timeout=15)
        case.close()


def main():
    trials = bounded_setting("TRIALS", 3, 1, 10)
    warmup = bounded_setting("ANALYTICS_WARMUP_S", 60, 0, 3600)
    duration = bounded_setting("ANALYTICS_DURATION_S", 300, 1, 3600)
    lab = json.loads((control.RESULTS / "lab.json").read_text())
    if lab["phase"] != "ready":
        raise RuntimeError("run make docker-up and make up first")
    with (control.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        control.owned_inventory()
        batch = control.RESULTS / "htap" / uuid.uuid4().hex
        config = {
            "created_ns": time.time_ns(),
            "trials": trials,
            "warmup_s": warmup,
            "duration_s": duration,
            "rate": int(control.ENV.get("RATE", 50)),
            "generation": lab["generation"],
            "seed_rows": lab["rows"],
            "query_groups": 100,
            "cache_state": "repeated queries after warmup; engine/OS hit ratios and page residency are not instrumented; no cold-cache claim",
            "scope": "Fixed seed analytical subset under append-only traffic; not arbitrary UPDATE/DELETE HTAP or saturated writer throughput. Live tails accumulate and are excluded by id predicate; their complete bodies remain verified.",
            "source_sha256": {
                str(p.relative_to(control.ROOT)): hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
                for p in [
                    Path(__file__),
                    *sorted((control.ROOT / "orders-workload").glob("*.go")),
                    control.ROOT / "docker-compose.yml",
                    control.ROOT / "scripts/control.py",
                    control.ROOT / "scripts/resources.py",
                    control.ROOT / "orders-workload" / "go.mod",
                    control.ROOT / "orders-workload" / "go.sum",
                    control.ROOT / "orders-workload" / "Dockerfile",
                    control.ROOT / "config/contract.json",
                ]
            },
        }
        control.save(batch / "config.json", config)
        headers = sorted(
            control.RESULTS.glob("environment-*.json"),
            key=lambda p: p.stat().st_mtime_ns,
        )
        control.save(batch / "environment.json", json.loads(headers[-1].read_text()))
        results = []
        for trial in range(trials):
            # Alternate backend and cell order. Both engines remain identically capped.
            backends = (
                control.BACKENDS
                if trial % 2 == 0
                else tuple(reversed(control.BACKENDS))
            )
            for concurrent in (False, True) if trial % 2 == 0 else (True, False):
                for backend in backends:
                    print(
                        f"trial={trial + 1} backend={backend} live={concurrent}",
                        flush=True,
                    )
                    results.append(
                        cell(backend, concurrent, trial + 1, warmup, duration)
                    )
                    control.save(
                        batch / "results.json",
                        {
                            "config": config,
                            "cells": results,
                            "complete": len(results) == trials * 4,
                        },
                    )
        print("HTAP result directory:", batch, flush=True)


if __name__ == "__main__":
    started = time.monotonic()
    started_ns = time.time_ns()
    before = resource.getrusage(resource.RUSAGE_SELF)
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        main()
    finally:
        after = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        control.save(
            control.RESULTS
            / "controller-costs"
            / ("htap-" + uuid.uuid4().hex + ".json"),
            {
                "started_ns": started_ns,
                "ended_ns": time.time_ns(),
                "wall_seconds": time.monotonic() - started,
                "controller_cpu_core_seconds": after.ru_utime
                + after.ru_stime
                - before.ru_utime
                - before.ru_stime,
                "controller_peak_rss_bytes": host_rss_bytes(after.ru_maxrss),
                "child_cpu_core_seconds": children.ru_utime
                + children.ru_stime
                - children_before.ru_utime
                - children_before.ru_stime,
                "child_peak_rss_bytes": host_rss_bytes(children.ru_maxrss),
                "host_system": os.uname().sysname,
                "scope": "Host controller and Docker CLI children only; ru_maxrss is bytes on Darwin and KiB on Linux. Engines/Go clients use separate Linux counters. Child peak is the largest child, not a simultaneous aggregate.",
            },
        )
