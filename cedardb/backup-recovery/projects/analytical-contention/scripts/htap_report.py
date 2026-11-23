"""Publish one explicit complete HTAP cohort; retain every trial and resource scope."""

import argparse
import datetime
import hashlib
import json
import math
import statistics
from pathlib import Path

import control
from htap import query_summary
from report import native_values, table
from resources import summarize_records


def verify_query_evidence(records, recorded, warmup_s, duration_s):
    if any(r.get("kind") == "analytic-query" and not r["passed"] for r in records):
        raise RuntimeError("failed raw query, including warmup")
    recomputed = query_summary(records)
    for key, actual in recomputed.items():
        if recorded.get(key) != actual:
            raise RuntimeError("query summary differs from raw evidence: " + key)
    direct = {
        r["phase"]: r["elapsed_ns"]
        for r in records
        if r.get("kind") == "phase-end" and "elapsed_ns" in r
    }
    if direct:
        if set(direct) != {"warmup", "measurement"}:
            raise RuntimeError("incomplete monotonic phase evidence")
        if direct["warmup"] < warmup_s * 1e9:
            raise RuntimeError("warmup window is shorter than declared")
        if direct["measurement"] < duration_s * 1e9:
            raise RuntimeError("measurement window is shorter than declared")
        return {
            "phase_clock": "monotonic",
            "warmup_seconds": direct["warmup"] / 1e9,
            "measurement_seconds": direct["measurement"] / 1e9,
            "scope": "Direct native monotonic elapsed controls duration gates and query-rate denominators. UTC timestamps are retained for resource/history correlation; no legacy shortfall tolerance applies.",
        }
    # Phase deadlines use time.Since (monotonic); UnixNano markers do not.
    # Accept at most 10ms of UTC discrepancy only with the declared phase
    # markers and an independently recorded monotonic main interval. The
    # source/image gates below bind this inference to the actual loop code.
    declared = {
        r["phase"]: r.get("duration_s")
        for r in records
        if r.get("kind") == "phase-start"
    }
    mains = [
        r["main_wall_seconds"]
        for r in records
        if r.get("kind") == "process-resources" and "main_wall_seconds" in r
    ]
    monotonic_support = (
        declared == {"warmup": warmup_s, "measurement": duration_s}
        and len(mains) == 1
        and mains[0] >= warmup_s + duration_s
    )
    shortfall = max(0, duration_s - recomputed["measured_seconds"])
    if shortfall and not (monotonic_support and shortfall <= 0.010):
        raise RuntimeError("measurement window is shorter than declared")
    starts = [
        r["started_ns"]
        for r in records
        if r.get("kind") == "phase-start" and r["phase"] == "warmup"
    ]
    ends = [
        r["ended_ns"]
        for r in records
        if r.get("kind") == "phase-end" and r["phase"] == "warmup"
    ]
    warmup_shortfall = (
        max(0, warmup_s - (ends[0] - starts[0]) / 1e9)
        if len(starts) == 1 and len(ends) == 1
        else None
    )
    if warmup_shortfall is None or (
        warmup_shortfall and not (monotonic_support and warmup_shortfall <= 0.010)
    ):
        raise RuntimeError("warmup window is missing or shorter than declared")
    return {
        "measurement_utc_shortfall_ms": shortfall * 1000,
        "warmup_utc_shortfall_ms": warmup_shortfall * 1000,
        "monotonic_main_seconds": mains[0] if mains else None,
        "utc_discrepancy_budget_ms": 10,
        "scope": "Runtime phase deadlines and per-query latencies use Go monotonic time. UTC markers determine the logged rate/resource window. Up to 10ms UTC shortfall requires declared phase lengths, completed phases, the verified monotonic-loop source/image and main duration >= warmup+measurement; larger discrepancies fail. Exact per-phase monotonic elapsed was not logged in this cohort.",
    }


def verify_resource_evidence(samples, recorded, start, end, backend, concurrent):
    measured = [s for s in samples if start <= s["started_ns"] and s["ended_ns"] <= end]
    recomputed = summarize_records(measured)
    if recomputed != recorded:
        raise RuntimeError("resource summary differs from raw measurement samples")
    required = {backend, "analytics"} | ({"writer", "reader"} if concurrent else set())
    if set(recomputed) != required:
        raise RuntimeError("resource evidence omits a required workload role")
    for role, metrics in recomputed.items():
        if (
            metrics["observed_cpu_core_seconds"] is None
            or metrics["sampled_charged_memory_bytes_min_mean_max"] is None
        ):
            raise RuntimeError(
                "resource evidence lacks CPU/memory observations: " + role
            )


def workload_comparisons(summaries):
    indexed = {(s["backend"], s["concurrent"]): s for s in summaries}
    comparisons = []

    def lower(selected, key):
        if math.isclose(selected[0][key], selected[1][key], rel_tol=1e-12):
            return "tie"
        return min(selected, key=lambda s: s[key])["backend"]

    for concurrent in (False, True):
        selected = [indexed[(backend, concurrent)] for backend in control.BACKENDS]
        faster, slower = sorted(
            selected, key=lambda s: s["queries_per_second_median"], reverse=True
        )
        comparisons.append(
            {
                "concurrent": concurrent,
                "higher_median_throughput_backend": "tie"
                if math.isclose(
                    faster["queries_per_second_median"],
                    slower["queries_per_second_median"],
                    rel_tol=1e-12,
                )
                else faster["backend"],
                "median_throughput_ratio": faster["queries_per_second_median"]
                / slower["queries_per_second_median"],
                "lower_observed_db_cpu_per_query_backend": lower(
                    selected, "median_observed_db_cpu_core_seconds_per_query"
                ),
                "lower_sampled_db_memory_backend": lower(
                    selected, "median_sampled_db_mean_memory_bytes"
                ),
                "scope": "Descriptive medians for this fixed query/traffic condition; no significance test or universal engine ranking. CPU includes observer work and excludes unsampled partial intervals.",
            }
        )
    interference = [
        {
            "backend": backend,
            "concurrent_query_rate_change_percent": 100
            * (
                indexed[(backend, True)]["queries_per_second_median"]
                / indexed[(backend, False)]["queries_per_second_median"]
                - 1
            ),
        }
        for backend in control.BACKENDS
    ]
    return comparisons, interference


def publish(folder):
    raw = json.loads((folder / "results.json").read_text())
    config, cells = raw["config"], raw["cells"]
    if not raw["complete"] or len(cells) != config["trials"] * 4:
        raise RuntimeError("cohort is incomplete")
    if config["trials"] < 3 or config["duration_s"] < 300 or config["warmup_s"] < 60:
        raise RuntimeError("short qualification is not a sustained repeated benchmark")
    expected = {
        (backend, concurrent, trial)
        for backend in control.BACKENDS
        for concurrent in (False, True)
        for trial in range(1, config["trials"] + 1)
    }
    actual = {(c["backend"], c["concurrent"], c["trial"]) for c in cells}
    if actual != expected or any(not c["passed"] for c in cells):
        raise RuntimeError("missing, duplicate or failed benchmark cell")
    if len({c["workload_image"] for c in cells}) != 1:
        raise RuntimeError("different workload images were used")
    for name, digest in config["source_sha256"].items():
        if hashlib.sha256((control.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("runtime source changed since the cohort: " + name)
    for cell in cells:
        if cell["verification"]["missing_required_acks"]:
            raise RuntimeError("acknowledged data missing")
        if (control.RESULTS / "histories" / cell["history"] / "failure.json").exists():
            raise RuntimeError("failed history must remain failed")
        cell["native_before"] = native_values(cell["native_before"])
        cell["native_after"] = native_values(cell["native_after"])
        if (
            cell["native_after"]["cpus"] != 4
            or cell["native_after"]["memory_limit_bytes"] != 6 * 1024**3
        ):
            raise RuntimeError("database resource budgets differ")
        history = control.RESULTS / "histories" / cell["history"]
        if (history / "timing-audit-discrepancy.json").exists():
            cell["original_utc_audit_discrepancy"] = json.loads(
                (history / "timing-audit-discrepancy.json").read_text()
            )
        records = control.journal(history / "analytics.log")
        if any(
            r.get("kind") == "analytic-query"
            and (
                r["seed_rows"] != config["seed_rows"]
                or r["verified_groups"] != config["query_groups"]
            )
            for r in records
        ):
            raise RuntimeError("query used a different input size or group contract")
        cell["clock_audit"] = verify_query_evidence(
            records, cell["query"], config["warmup_s"], config["duration_s"]
        )
        queries = [
            r
            for r in records
            if r.get("kind") == "analytic-query" and r["phase"] == "measurement"
        ]
        times = sorted(r["query_fetch_ms"] for r in queries)
        verify_resource_evidence(
            control.journal(history / "analytics-resources.jsonl"),
            cell["measurement_resources"],
            queries[0]["started_ns"],
            queries[-1]["ended_ns"],
            cell["backend"],
            cell["concurrent"],
        )
        if len(times) != cell["query"]["queries"]:
            raise RuntimeError("query sample count differs from raw evidence")
        cell["query"]["p999_ms"] = (
            times[math.ceil(0.999 * len(times)) - 1] if len(times) >= 10000 else None
        )
        cell["query"]["p999_scope"] = (
            "Reported only with >=10,000 samples (at least ten observations in the top 0.1%); otherwise unavailable."
        )
        if cell["concurrent"]:
            start, end = queries[0]["started_ns"], queries[-1]["ended_ns"]
            for role, outcomes in cell["live_workload"].items():
                events = control.journal(history / (role + ".jsonl"))
                for kind, metrics in outcomes.items():
                    times = sorted(
                        e["elapsed_ns"] / 1e6
                        for e in events
                        if e["kind"] == kind and start <= e["started_ns"] < end
                    )
                    if len(times) != metrics["count"]:
                        raise RuntimeError(
                            "operation sample count differs from raw history"
                        )
                    metrics["mean_ms"] = statistics.mean(times) if times else None
                    metrics["p95_ms"] = (
                        times[math.ceil(0.95 * len(times)) - 1] if times else None
                    )
                    metrics["p999_ms"] = (
                        times[math.ceil(0.999 * len(times)) - 1]
                        if len(times) >= 10000
                        else None
                    )
    summaries = []
    for backend in control.BACKENDS:
        for concurrent in (False, True):
            selected = [
                c
                for c in cells
                if c["backend"] == backend and c["concurrent"] == concurrent
            ]
            rates = [c["query"]["queries_per_second"] for c in selected]
            p50 = [c["query"]["p50_ms"] for c in selected]
            rate_quartiles = statistics.quantiles(rates, n=4, method="inclusive")
            p50_quartiles = statistics.quantiles(p50, n=4, method="inclusive")
            summaries.append(
                {
                    "backend": backend,
                    "concurrent": concurrent,
                    "queries_per_second_median": statistics.median(rates),
                    "queries_per_second_min": min(rates),
                    "queries_per_second_max": max(rates),
                    "queries_per_second_iqr": rate_quartiles[2] - rate_quartiles[0],
                    "median_trial_p50_ms": statistics.median(p50),
                    "trial_p50_iqr_ms": p50_quartiles[2] - p50_quartiles[0],
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
                    "scope": "Median of trial summaries; percentiles are not pooled. IQR uses inclusive linearly interpolated quartiles across trials.",
                }
            )
    published = {
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "date_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "config": config,
        "cells": cells,
        "summaries": summaries,
        "process_layout_audit": json.loads(
            (control.RESULTS / "process-layout-audit.json").read_text()
        )
        if (control.RESULTS / "process-layout-audit.json").exists()
        else None,
        "environment": json.loads((folder / "environment.json").read_text()),
        "source_result_directory": str(folder.relative_to(control.ROOT)),
        "cache_validation": "The configured warm profile means repeated queries after the timed warmup, not measured engine/OS hit ratios or proven page residency. No OS cache manipulation or cold-cache proof.",
        "latency_precision": "Raw per-query monotonic durations converted from nanoseconds to float64 milliseconds; no histogram buckets. Nanosecond units do not establish clock accuracy. p50 is the sample median; p95/p99/p99.9 use nearest rank.",
        "resource_scope": "Database measurement-window sampled CPU/memory/block/network counters; reset/partial intervals excluded. Native peaks cover database process lifetime including warmup and verification, not only the measurement. Native compression, whole-store allocation and CSV gzip are separate layers. Container counters do not include all Docker VM/host work.",
        "controller_costs": [
            cost
            for p in sorted((control.RESULTS / "controller-costs").glob("htap-*.json"))
            if (cost := json.loads(p.read_text())).get("started_ns", 0)
            <= config["created_ns"]
            <= cost.get("ended_ns", 0)
        ],
    }
    published["workload_comparisons"], published["concurrency_impact"] = (
        workload_comparisons(summaries)
    )
    if (
        published["process_layout_audit"]
        and published["process_layout_audit"].get("generation") != config["generation"]
    ):
        published["process_layout_audit"] = None
    generation = control.RESULTS / "generations" / config["generation"]
    published["setup"] = {
        "resource_summary": json.loads(
            (generation / "setup-resource-summary.json").read_text()
        ),
        "native_before_load": {
            k: native_values(v)
            for k, v in json.loads(
                (generation / "before-load.json").read_text()
            ).items()
        },
        "native_after_load": {
            k: native_values(v)
            for k, v in json.loads((generation / "after-load.json").read_text()).items()
        },
        "seed_processes": {
            backend: json.loads((generation / (backend + "-seed.json")).read_text())
            for backend in control.BACKENDS
        },
        "scope": "One shared initial fixture per backend, not seed work repeated or added to each query cell. Import CPU/wall, native memory/compression/storage and sampled I/O remain separate from query costs.",
    }
    if len(published["controller_costs"]) != 1:
        raise RuntimeError(
            "cohort requires its own completed controller resource receipt"
        )
    control.save(control.ROOT / "projects/analytical-contention/reports/HTAP_RESULTS.json", published)
    rows = []
    for s in summaries:
        rows.append(
            [
                s["backend"],
                "live appends + point reads" if s["concurrent"] else "alone",
                f"{s['queries_per_second_median']:.2f} ({s['queries_per_second_min']:.2f} … {s['queries_per_second_max']:.2f})",
                f"{s['queries_per_second_iqr']:.2f}",
                f"{s['median_trial_p50_ms']:.3f}",
            ]
        )
    trial_rows = []
    for c in cells:
        resource = c["measurement_resources"][c["backend"]]
        memory = resource["sampled_charged_memory_bytes_min_mean_max"]
        trial_rows.append(
            [
                c["backend"],
                c["trial"],
                c["concurrent"],
                c["query"]["queries"],
                f"{c['query']['p50_ms']:.3f} / {c['query']['p99_ms']:.3f}",
                f"{resource['observed_cpu_core_seconds']:.2f}",
                f"{memory[1] / 1048576:.1f} / {memory[2] / 1048576:.1f}",
                f"{c['native_after']['database_allocated_bytes'] / 1048576:.1f}",
            ]
        )
    text = "\n\n".join(
        [
            "## Sustained repeated results",
            f"**{published['date_utc'][:10]}**: {len(cells)} complete cells, {config['trials']} fresh database-process trials per backend/traffic condition, {config['warmup_s']} s warmup and at least {config['duration_s']} s measured per cell. All analytical groups and complete seeded/acknowledged bodies passed verification. Input is fixed and repeatedly queried after warmup; writes are append-only; no general HTAP or cold-cache winner is established.",
            table(
                [
                    "Backend",
                    "Traffic",
                    "Measured queries/s median (min … max)",
                    "Rate IQR",
                    "Median trial p50 ms",
                ],
                rows,
            ),
            table(
                [
                    "Traffic",
                    "Higher median query throughput",
                    "Median rate ratio",
                    "Lower observed DB CPU/query",
                    "Lower sampled DB mean memory",
                ],
                [
                    [
                        "concurrent" if c["concurrent"] else "alone",
                        c["higher_median_throughput_backend"],
                        f"{c['median_throughput_ratio']:.2f}×",
                        c["lower_observed_db_cpu_per_query_backend"],
                        c["lower_sampled_db_memory_backend"],
                    ]
                    for c in published["workload_comparisons"]
                ],
            ),
            "These are descriptive resource/throughput comparisons for one fixed aggregate, not significance tests or a universal ranking. Concurrent query-rate changes relative to each engine's alone case: "
            + "; ".join(
                f"{c['backend']} {c['concurrent_query_rate_change_percent']:+.1f}%"
                for c in published["concurrency_impact"]
            )
            + ". CPU/query uses observed sampled intervals, excludes initial/final partial intervals and includes database-side observer costs; it is not a complete service-demand measurement.",
            table(
                [
                    "Backend",
                    "Trial",
                    "Concurrent",
                    "Samples",
                    "p50 / p99 ms",
                    "Observed DB CPU core-s",
                    "Sampled DB memory mean/max MiB",
                    "DB allocated MiB",
                ],
                trial_rows,
            ),
            "Per-trial query latency, actual throughput/window, independent read/write OK/error/ambiguous rates and latency, CPU utilization/core-seconds, memory, compression and disk/I/O bytes/rates are retained in [HTAP_RESULTS.json](HTAP_RESULTS.json). Raw logs/witnesses remain ignored. Native cgroup memory peaks include warmup and final verification; sampled measurement-window peaks are separate. Native container I/O counters may omit Docker Desktop cache/VM traffic. Unavailable IOPS/latency/queue metrics are not zeros.",
        ]
    )
    path = control.ROOT / "projects/analytical-contention/docs/HTAP.md"
    source = path.read_text()
    source = source.split("<!-- htap-results -->", 1)[0].rstrip()
    source = source.replace(
        "three-trial sustained cohort is still running.",
        "three-trial sustained cohort passed; results follow below.",
    )
    path.write_text(
        source + "\n\n<!-- htap-results -->\n\n" + text + "\n<!-- /htap-results -->\n"
    )
    print("Published", len(cells), "HTAP cells")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "results", type=Path, help="Explicit results/htap/<run-id> directory"
    )
    publish(parser.parse_args().results.resolve())
