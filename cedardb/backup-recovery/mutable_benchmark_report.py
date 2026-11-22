"""Reconcile a complete sustained mutation/query cohort before publishing numbers."""

import argparse
import datetime
import hashlib
import json
import math
import statistics
from pathlib import Path

import control
import mutable_oracle
from htap_report import verify_query_evidence, workload_comparisons
from mutable_benchmark import live_summary, measured_samples
from report import native_values, table
from resources import parse_native_io, summarize_records


def load(path):
    return json.loads(path.read_text())


def number(value, divisor=1):
    return "unavailable" if value is None else f"{value / divisor:.3f}"


def compact_environment(value):
    result = {
        key: value[key]
        for key in (
            "created_ns",
            "disk_class",
            "kubernetes",
            "topology",
            "source_revision",
        )
    }
    result["host"] = dict(
        zip(
            ("system", "kernel", "architecture"),
            (value["host"][0], value["host"][2], value["host"][4]),
        )
    )
    result["docker"] = {
        key: value["docker_info"][key]
        for key in (
            "ServerVersion",
            "KernelVersion",
            "Architecture",
            "NCPU",
            "MemTotal",
            "Driver",
            "CgroupVersion",
        )
    }
    result["versions"] = {
        backend: [row for row in records if isinstance(row, list)]
        for backend, records in value["versions"].items()
    }
    result["databases"] = {}
    for backend, info in value["images_and_limits"].items():
        limits, configuration = info["HostConfig"], info["Config"]
        result["databases"][backend] = {
            "image_id": info["Image"],
            "image_reference": configuration["Image"],
            "cpus": limits["NanoCpus"] / 1e9,
            "memory_limit_bytes": limits["Memory"],
            "memory_swap_limit_bytes": limits["MemorySwap"],
            "command": configuration["Cmd"],
            "entrypoint": configuration["Entrypoint"],
            "tuning_environment": {
                key: val
                for item in configuration["Env"]
                for key, _, val in [item.partition("=")]
                if key in {"PARALLEL", "BUFFERSIZE", "WORKMEMSIZE"}
            },
            "storage": [
                {
                    "type": mount["Type"],
                    "destination": mount["Destination"],
                    "read_write": mount["RW"],
                }
                for mount in info["Mounts"]
            ],
        }
    result["scope"] = (
        "Selected environment/version/limit fields. The cohort config contains actual hardware, mutable contract and runtime source hashes; full inspection dumps remain in the ignored environment artifact. Source revision here is the initial capture revision, not a substitute for the cohort's tested file hashes."
    )
    return result


def cohort_gate(raw):
    config, cells = raw["config"], raw["cells"]
    expected = {
        (backend, concurrent, trial)
        for backend in control.BACKENDS
        for concurrent in (False, True)
        for trial in range(1, config["trials"] + 1)
    }
    if (
        not raw.get("complete")
        or not raw.get("finalized")
        or config["diagnostic_only"]
        or config["rows"] < 1000000
        or config["trials"] < 3
        or config["warmup_s"] < 60
        or config["duration_s"] < 300
    ):
        raise RuntimeError(
            "require a finalized million-row, three-trial sustained cohort"
        )
    if (
        len(cells) != len(expected)
        or {(c["backend"], c["concurrent"], c["trial"]) for c in cells} != expected
    ):
        raise RuntimeError("missing or duplicate matrix cells")
    if any(
        not c["passed"] or c["workload_image"] != config["workload_image"]
        for c in cells
    ):
        raise RuntimeError("failed cell or changed application image")


def operation_timings(events):
    output = {}
    for operation in sorted({e["operation"] for e in events if e["kind"] != "intent"}):
        output[operation] = {}
        for kind in ("ok", "fail", "ambiguous", "mismatch"):
            chosen = [
                e for e in events if e["kind"] == kind and e["operation"] == operation
            ]
            stages = {"request": [e["elapsed_ns"] for e in chosen]}
            for stage in sorted({s for e in chosen for s in e.get("stage_ns", {})}):
                stages[stage] = [
                    e["stage_ns"][stage]
                    for e in chosen
                    if stage in e.get("stage_ns", {})
                ]
            output[operation][kind] = {
                stage: {
                    "count": len(values),
                    "mean_ms": statistics.mean(values) / 1e6,
                    "p50_ms": statistics.median(values) / 1e6,
                    "p95_ms": sorted(values)[math.ceil(0.95 * len(values)) - 1] / 1e6,
                    "p99_ms": sorted(values)[math.ceil(0.99 * len(values)) - 1] / 1e6,
                    "p999_ms": sorted(values)[math.ceil(0.999 * len(values)) - 1] / 1e6
                    if len(values) >= 10000
                    else None,
                }
                for stage, values in stages.items()
                if values
            }
    return output


def verify_fresh_processes(cells, trials):
    for backend in control.BACKENDS:
        starts = [c["database_started_at"] for c in cells if c["backend"] == backend]
        if len(starts) != trials * 2 or len(set(starts)) != len(starts):
            raise RuntimeError("database process generation was reused across cells")


def verify_cell(cell, config):
    root = control.RESULTS / "mutable" / cell["identity"]
    if not root.resolve().is_relative_to((control.RESULTS / "mutable").resolve()):
        raise RuntimeError("case path escaped the example")
    if any((root / name).exists() for name in ("failure.json", "invalidated.json")):
        raise RuntimeError("failed evidence cannot qualify")
    if (
        load(root / "benchmark-result.json") != cell
        or not load(root / "cleanup.json")["schema_dropped_after_verification"]
    ):
        raise RuntimeError("cell result/cleanup differs from cohort")
    case = load(root / "config.json")
    if (
        any(
            case[key] != config[key]
            for key in ("rows", "pairs", "warmup_s", "duration_s")
        )
        or case["generation"] != config["base_generation"]
    ):
        raise RuntimeError("case fixture/window differs")
    if any(
        config["source_sha256"].get(name) != digest
        for name, digest in case["source_sha256"].items()
    ):
        raise RuntimeError("case source differs from cohort")
    proof = mutable_oracle.reconcile(
        root / "rows.csv",
        root / "receipts.csv",
        root / "writer.jsonl",
        config["rows"],
        config["pairs"],
        root / "reader.jsonl" if cell["concurrent"] else None,
    )
    if proof != {k: v for k, v in cell["verification"].items() if k != "compression"}:
        raise RuntimeError("full body/receipt/history proof differs")
    records = control.journal(root / "analytics.log")
    clock = verify_query_evidence(
        records, cell["query"], config["warmup_s"], config["duration_s"]
    )
    if any(
        r["seed_rows"] != config["rows"] or r["verified_groups"] != 100
        for r in records
        if r.get("kind") == "analytic-query"
    ):
        raise RuntimeError("query input/oracle differs")
    start = next(
        r["started_ns"]
        for r in records
        if r.get("kind") == "phase-start" and r["phase"] == "measurement"
    )
    end = next(
        r["ended_ns"]
        for r in records
        if r.get("kind") == "phase-end" and r["phase"] == "measurement"
    )
    measured = summarize_records(
        measured_samples(
            control.journal(root / "analytics-resources.jsonl"), start, end
        )
    )
    if measured != cell["measurement_resources"]:
        raise RuntimeError("resource summary differs from raw samples")
    expected_roles = {cell["backend"], "analytics"} | (
        {"reader", "writer"} if cell["concurrent"] else set()
    )
    if set(measured) != expected_roles or any(
        v["observed_cpu_core_seconds"] is None
        or v["sampled_charged_memory_bytes_min_mean_max"] is None
        for v in measured.values()
    ):
        raise RuntimeError("CPU/memory coverage incomplete")
    native_io = measured[cell["backend"]]["io_and_network"]
    if not all(
        any(
            k.startswith("native_block_")
            and k.endswith(suffix)
            and v["observed_seconds"] > 0
            for k, v in native_io.items()
        )
        for suffix in (
            "read_bytes",
            "write_bytes",
            "read_operations",
            "write_operations",
        )
    ):
        raise RuntimeError("native disk-byte/IOPS coverage incomplete")
    controls = [
        r
        for r in load(root / "negative-controls.json")
        if r.get("kind") == "mutable-negative-controls"
    ]
    if len(controls) != 1 or not controls[0]["passed"]:
        raise RuntimeError("native transactional controls missing")
    actors = {}
    for role in sorted(expected_roles - {cell["backend"]}):
        info = load(
            root
            / (
                "analytics-final.json"
                if role == "analytics"
                else role + "-stopped.json"
            )
        )
        if (
            info["State"]["Running"]
            or info["State"]["ExitCode"]
            or info["Image"] != config["workload_image"]
            or info["HostConfig"]["NanoCpus"] != 10**9
            or any(
                info["HostConfig"][k] != 512 * 1024**2 for k in ("Memory", "MemorySwap")
            )
        ):
            raise RuntimeError("application exit/image/budget differs")
        log = (
            records if role == "analytics" else control.journal(root / (role + ".log"))
        )
        native_process = [r for r in log if r.get("kind") == "process-resources"]
        if len(native_process) != 1:
            raise RuntimeError("missing native application resource counters")
        actors[role] = {"process_resources": native_process[0]}
        counters = parse_native_io(native_process[0]["io.stat"])
        lifetime = native_process[0]["main_wall_seconds"]
        actors[role]["lifetime_native_io"] = {
            "devices": {
                device: {
                    "cumulative": values,
                    "per_main_wall_second": {
                        key: value / lifetime for key, value in values.items()
                    },
                }
                for device, values in counters.items()
            },
            "scope": "Final native cgroup counters divided by main monotonic duration, including warmup/recovery/connection close. This is a lifetime observation, not measurement-phase IO. Empty devices mean no counters exposed, not proven zero traffic. Disk IO latency/queue depth unavailable. Witness bind-mount IO can be accounted outside the actor cgroup.",
        }
        if role != "analytics":
            events = [
                e
                for e in control.journal(root / (role + ".jsonl"))
                if e.get("ended_ns", 0) <= end
            ]
            recorded = live_summary(
                events, start, end, cell["query"]["measured_seconds"]
            )
            if (
                recorded != cell["live_workload"][role]
                or recorded["ok"]["count"] < 5
                or recorded["ok"]["count"]
                / max(sum(v["count"] for v in recorded.values()), 1)
                < 0.95
            ):
                raise RuntimeError("live success-rate evidence differs or fails")
            selected = [
                e
                for e in events
                if e["kind"] != "intent" and start <= e["started_ns"] < end
            ]
            actors[role]["measurement_operations"] = operation_timings(selected)
            if role == "writer" and any(
                not actors[role]["measurement_operations"].get(op, {}).get("ok")
                for op in ("paired-update", "delete-reinsert")
            ):
                raise RuntimeError("one declared mutation type never succeeded")
    normalized = dict(cell)
    started_at = cell["native_before"]["container"]["State"]["StartedAt"]
    for name in ("native_before", "native_initialized", "native_after"):
        native = cell[name]
        if native["container"]["State"]["StartedAt"] != started_at:
            raise RuntimeError("database process generation changed inside cell")
        normalized[name] = native_values(native)
        normalized[name].pop(
            "pid1_status"
        )  # Selected RSS/HWM and cgroup counters remain.
        if (
            normalized[name]["cpus"] != 4
            or normalized[name]["memory_limit_bytes"] != 6 * 1024**3
            or native["container"]["HostConfig"]["MemorySwap"] != 6 * 1024**3
            or normalized[name]["image_id"]
            != config["database_images"][cell["backend"]]
        ):
            raise RuntimeError("database image/budget differs")
    stopped = load(root / "completed-backend-stopped.json")
    if (
        stopped["State"]["Running"]
        or stopped["State"]["ExitCode"]
        or stopped["State"]["StartedAt"] != started_at
    ):
        raise RuntimeError("database did not stop cleanly after verification")
    normalized.update(
        actors=actors,
        database_started_at=started_at,
        clock_audit=clock,
        controller=load(root / "controller.json"),
        raw_result_sha256=hashlib.sha256(
            (root / "benchmark-result.json").read_bytes()
        ).hexdigest(),
        planner_observations=[
            {
                "source_file": str(path.relative_to(control.ROOT)),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "observation": load(path),
            }
            for path in sorted(root.glob("postgres-statistics-*.json"))
        ],
    )
    query_times = sorted(
        r["query_fetch_ms"]
        for r in records
        if r.get("kind") == "analytic-query" and r["phase"] == "measurement"
    )
    normalized["query_p999_ms"] = (
        query_times[math.ceil(0.999 * len(query_times)) - 1]
        if len(query_times) >= 10000
        else None
    )
    if (
        normalized["controller"]["cleanup_errors"]
        or not normalized["controller"]["scenario_passed"]
    ):
        raise RuntimeError("controller scenario/cleanup failed")
    return normalized


def functional_prerequisites(config):
    selected = {}
    for path in sorted(
        (control.RESULTS / "mutable").glob("*/config.json"),
        key=lambda p: p.stat().st_mtime_ns,
    ):
        case = load(path)
        result_path = path.parent / "result.json"
        if case["generation"] != config["base_generation"] or "concurrent" in case:
            continue
        if case["rows"] != config["rows"] or any(
            config["source_sha256"].get(name) != digest
            for name, digest in case["source_sha256"].items()
        ):
            continue
        root = path.parent
        if any((root / name).exists() for name in ("failure.json", "invalidated.json")):
            raise RuntimeError("current-generation functional prerequisite failed")
        if not result_path.exists():
            raise RuntimeError("current-generation functional prerequisite incomplete")
        result = load(result_path)
        if not result["passed"]:
            raise RuntimeError("current-generation functional prerequisite failed")
        if not load(root / "cleanup.json")["schema_dropped_after_verification"]:
            raise RuntimeError("functional cleanup missing")
        proof = mutable_oracle.reconcile(
            root / "rows.csv",
            root / "receipts.csv",
            root / "writer.jsonl",
            case["rows"],
            case["pairs"],
            root / "reader.jsonl",
        )
        if proof != {k: v for k, v in result["proof"].items() if k != "compression"}:
            raise RuntimeError("functional proof differs")
        for role in ("writer", "reader"):
            actor = load(root / (role + "-stopped.json"))
            if (
                actor["State"]["Running"]
                or actor["State"]["ExitCode"]
                or actor["Image"] != config["workload_image"]
            ):
                raise RuntimeError("functional application exit/image differs")
        if result["crash"]:
            dead = load(root / "killed.json")
            if dead["State"]["Running"] or dead["State"]["ExitCode"] != 137:
                raise RuntimeError("functional SIGKILL not proven")
        selected[(case["backend"], result["crash"])] = {
            "identity": case["identity"],
            "backend": case["backend"],
            "crash": result["crash"],
            "proof": result["proof"],
            "phases": result["phases"],
            "resources": result["resources"],
            "native_after": {
                key: value
                for key, value in native_values(result["native_after"]).items()
                if key != "pid1_status"
            },
            "raw_result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
        }
    if set(selected) != {(b, c) for b in control.BACKENDS for c in (False, True)}:
        raise RuntimeError(
            "require healthy and SIGKILL million-row prerequisites on the tested source/image"
        )
    return list(selected.values())


def publish(folder):
    folder = folder.resolve(strict=True)
    if not folder.is_relative_to((control.RESULTS / "mutable-benchmark").resolve()):
        raise RuntimeError("select an example-owned cohort")
    raw = load(folder / "results.json")
    cohort_gate(raw)
    config = raw["config"]
    for name, digest in config["source_sha256"].items():
        if hashlib.sha256((control.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("runtime source differs from tested source: " + name)
    cells = [verify_cell(cell, config) for cell in raw["cells"]]
    verify_fresh_processes(cells, config["trials"])
    prerequisites = functional_prerequisites(config)
    summaries = []
    for backend in control.BACKENDS:
        for concurrent in (False, True):
            selected = [
                c
                for c in cells
                if c["backend"] == backend and c["concurrent"] == concurrent
            ]
            rates = [c["query"]["queries_per_second"] for c in selected]
            quartiles = statistics.quantiles(rates, n=4, method="inclusive")
            summaries.append(
                {
                    "backend": backend,
                    "concurrent": concurrent,
                    "queries_per_second_median": statistics.median(rates),
                    "queries_per_second_min": min(rates),
                    "queries_per_second_max": max(rates),
                    "queries_per_second_iqr": quartiles[2] - quartiles[0],
                    "median_trial_p50_ms": statistics.median(
                        c["query"]["p50_ms"] for c in selected
                    ),
                    "median_trial_p99_ms": statistics.median(
                        c["query"]["p99_ms"] for c in selected
                    ),
                    "median_observed_db_cpu_core_seconds_per_query": statistics.median(
                        c["measurement_resources"][backend]["observed_cpu_core_seconds"]
                        / c["query"]["queries"]
                        for c in selected
                    ),
                    "median_sampled_db_mean_memory_bytes": statistics.median(
                        c["measurement_resources"][backend][
                            "sampled_charged_memory_bytes_min_mean_max"
                        ][1]
                        for c in selected
                    ),
                }
            )
    comparisons, interference = workload_comparisons(summaries)
    report = {
        "passed": True,
        "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_directory": str(folder.relative_to(control.ROOT)),
        "config": config,
        "environment": compact_environment(load(folder / "environment.json")),
        "raw_environment_sha256": hashlib.sha256(
            (folder / "environment.json").read_bytes()
        ).hexdigest(),
        "cells": cells,
        "functional_prerequisites": prerequisites,
        "summaries": summaries,
        "workload_comparisons": comparisons,
        "concurrency_impact": interference,
        "statistics_preparation": "This cohort does not issue explicit ANALYZE after copying each mutable table; native automatic statistics preparation remains enabled. Optional read-only catalog/EXPLAIN observations are retained per case with timestamp and administrative-client costs. The first PostgreSQL live cell briefly used additional 1-CPU/512-MiB one-shot observation clients; their actual CPU/RSS/cgroup costs are preserved in those observations. Later psql probes execute inside the existing database cap. Unobserved cases have no direct readiness proof. Results describe this preparation profile, not a comparison of optimally prepared engines.",
        "scope": "Single-node, warm-query, ticker-paced one-writer/one-reader HTAP; not saturation, arbitrary changing aggregates, serializability, independent-host durability or a universal ranking. Each cell restarts its database process and creates a fresh schema; base volumes/OS caches remain. Median of independent trial summaries, not pooled percentiles. CPU includes observers and excludes unsampled edges; native peaks cover full process lifetime. Native block counters are per-device VM/cgroup accounting, not host physical IO; do not sum duplicate device layers or Docker/native counters. Physical disk IO latency/queue depth unavailable.",
    }
    control.save(control.ROOT / "MUTABLE_BENCHMARK_RESULTS.json", report)
    lines = [
        "# Sustained mutable-order analytical comparison",
        "",
        report["scope"],
        "",
        report["statistics_preparation"],
        "",
        f"{config['rows']:,} rows; {config['trials']} fresh-process trials per cell; {config['warmup_s']} s warmup and ≥{config['duration_s']} s monotonic measurement. 4 CPUs / 6 GiB per database; 1 CPU / 512 MiB per actor, swap disabled. The inactive database is stopped.",
        "",
        table(
            [
                "Backend",
                "Live mutations/readers",
                "Queries/s median (min … max)",
                "Trial p50 / p99 median",
                "DB sampled CPU/query",
                "DB sampled mean memory median",
            ],
            [
                [
                    s["backend"],
                    str(s["concurrent"]),
                    f"{s['queries_per_second_median']:.3f} ({s['queries_per_second_min']:.3f} … {s['queries_per_second_max']:.3f})",
                    f"{s['median_trial_p50_ms']:.3f} / {s['median_trial_p99_ms']:.3f} ms",
                    f"{s['median_observed_db_cpu_core_seconds_per_query']:.6f} core-s",
                    f"{s['median_sampled_db_mean_memory_bytes'] / 1024**2:.2f} MiB",
                ]
                for s in summaries
            ],
        ),
        "",
        "Full per-trial counts, complete body/receipt reconciliation, query and separate successful/error mutation/read latency, native CPU/RSS/charged-memory, compression, whole-store disk allocation and per-device byte/IOPS rates are in [MUTABLE_BENCHMARK_RESULTS.json](MUTABLE_BENCHMARK_RESULTS.json). Sampling is approximately once per second plus sequential probes; actual intervals are retained. Application lifetime native counters include warmup and shutdown; measurement counters are separate. Native compression and verified CSV gzip are different layers. Nanosecond timers do not imply nanosecond accuracy. See [the workload/contract and failure history](MUTABLE.md).",
        "",
    ]
    lines.extend(
        [
            f"The immutable base CSV is **{config['input_fixture']['cedar']['csv_bytes']:,} bytes**, SHA-256 `{config['input_fixture']['cedar']['csv_sha256']}` on both backends. Mutable exports and their checksums are separate per trial. Data has 10,000 customers, 100 aggregate groups, non-null typed numeric columns and 256-byte string payloads; generation and mutation formulas are in the checked-in contract.",
            "",
            table(
                [
                    "Backend / live / trial",
                    "Device",
                    "Read / write MiB",
                    "Read / write IOPS",
                    "Whole store logical / allocated MiB",
                    "Export gzip1 ratio",
                ],
                [
                    [
                        f"{c['backend']} / {c['concurrent']} / {c['trial']}",
                        device,
                        f"{number(io['native_block_' + device + '_read_bytes']['observed_delta'], 1024**2)} / {number(io['native_block_' + device + '_write_bytes']['observed_delta'], 1024**2)}",
                        f"{number(io['native_block_' + device + '_read_operations']['per_second'])} / {number(io['native_block_' + device + '_write_operations']['per_second'])}",
                        f"{c['native_after']['database_logical_bytes'] / 1024**2:.3f} / {c['native_after']['database_allocated_bytes'] / 1024**2:.3f}",
                        f"{c['verification']['compression'][0]['ratio']:.3f}×",
                    ]
                    for c in cells
                    for io in [
                        c["measurement_resources"][c["backend"]]["io_and_network"]
                    ]
                    for device in sorted(
                        {
                            key.split("_")[2]
                            for key in io
                            if key.startswith("native_block_")
                        }
                    )
                ],
            ),
            "",
            "Block byte deltas and IOPS cover the observed measurement intervals, not seed/export work. Whole-store bytes are a post-verification snapshot including the immutable base, mutable tables, receipts, indexes, WAL and unreclaimed engine files. The independent CSV compression ratio is not native engine compression. Native table encoding/compression snapshots are retained in JSON. Application per-role CPU, memory and native I/O observations are also retained; absent device counters are unavailable, not zero. The configured whole-stack budget is 5 CPUs / 6.5 GiB alone or 7 CPUs / 7.5 GiB with all three workload roles, excluding Docker VM/host/controller costs.",
            "",
        ]
    )
    (control.ROOT / "MUTABLE_BENCHMARK.md").write_text("\n".join(lines))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cohort", type=Path)
    publish(parser.parse_args().cohort)
