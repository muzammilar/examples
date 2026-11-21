"""Publish only complete, equally budgeted ingestion trials and correctness proof."""

import json
import statistics
import sys

import experiment as bench
from write_results import table


def load(identity):
    if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
        raise ValueError("invalid ingestion run ID")
    root = bench.RESULTS / "ingestion" / identity
    setup = json.loads((root / "setup.json").read_text())
    report = json.loads((root / "result.json").read_text())
    expected = (
        2
        * len(setup["languages"])
        * len(setup["methods"])
        * len(setup["batches"])
        * setup["trials"]
    )
    if (
        not report["passed"]
        or len(report["cases"]) != expected
        or list(root.glob("*/failure.json"))
    ):
        raise RuntimeError("run is incomplete or contains an unqualified failed case")
    return setup, report


def summarize(setup, cases):
    summaries = []
    for method in setup["methods"]:
        for batch in setup["batches"]:
            for language in setup["languages"]:
                for engine in ("rayforce", "duckdb"):
                    group = [
                        c
                        for c in cases
                        if (c["method"], c["batch_size"], c["language"], c["engine"])
                        == (method, batch, language, engine)
                    ]
                    if sorted(c["trial"] for c in group) != list(
                        range(1, setup["trials"] + 1)
                    ):
                        raise RuntimeError("missing or duplicate matrix trial")
                    rates = [c["writes"]["rows_per_second"] for c in group]
                    quartiles = statistics.quantiles(rates, n=4, method="inclusive")
                    summaries.append(
                        {
                            "method": method,
                            "batch_size": batch,
                            "language": language,
                            "engine": engine,
                            "median_rows_per_second": statistics.median(rates),
                            "min_rows_per_second": min(rates),
                            "max_rows_per_second": max(rates),
                            "iqr_rows_per_second": quartiles[2] - quartiles[0],
                            "median_request_p50_ms": statistics.median(
                                c["writes"]["request_p50_ms"] for c in group
                            ),
                            "median_request_p99_ms": statistics.median(
                                c["writes"]["request_p99_ms"] for c in group
                            ),
                            "median_cpu_core_seconds": statistics.median(
                                c["native"]["cgroup_cpu_seconds"] for c in group
                            ),
                            "median_ingestion_cpu_seconds": statistics.median(
                                c["writes"]["window_cpu_seconds"] for c in group
                            ),
                            "median_import_ms": statistics.median(
                                c["phases"]["load"]["wall_seconds"] * 1000
                                for c in group
                            ),
                            "median_export_ms": statistics.median(
                                c["phases"]["export"]["wall_seconds"] * 1000
                                for c in group
                            ),
                            "median_process_peak_rss_mib": statistics.median(
                                c["native"]["process_peak_rss_kib"] / 1024
                                for c in group
                            ),
                            "median_charged_memory_mean_mib": statistics.median(
                                c["native"]["sampled_resources"][
                                    "sampled_mean_charged_memory_bytes"
                                ]
                                / 1048576
                                for c in group
                            ),
                            "median_cgroup_peak_memory_mib": statistics.median(
                                c["native"]["cgroup_peak_memory_bytes"] / 1048576
                                for c in group
                            ),
                            "median_cpu_utilization_percent_one_core_100": statistics.median(
                                c["native"]["cpu_utilization_percent_one_core_100"]
                                for c in group
                            ),
                        }
                    )
    return summaries


def publish(benchmark_id, correctness_id):
    setup, report = load(benchmark_id)
    checked_setup, checked = load(correctness_id)
    if (
        setup["trials"] < 3
        or setup["fixture"]["sha256"] != checked_setup["fixture"]["sha256"]
    ):
        raise RuntimeError(
            "comparison requires three trials and the same correctness fixture"
        )
    columnar = "columnar" in setup["methods"]
    filename = "COLUMNAR_RESULTS.json" if columnar else "INGESTION_RESULTS.json"
    section = "columnar-results" if columnar else "ingestion-results"
    heading = "Columnar Appender comparison" if columnar else "Ingestion comparison"
    cases = report["cases"]
    if not set(setup["methods"]) <= set(checked_setup["methods"]) or not set(
        setup["batches"]
    ) <= set(checked_setup["batches"]):
        raise RuntimeError("correctness does not cover the measured paths/batches")
    images = {c["native"]["image"] for c in cases + checked["cases"]}
    cpus = {c["native"]["cpus"] for c in cases + checked["cases"]}
    if (
        len(images) != 1
        or len(cpus) != 1
        or any(
            c["native"]["memory_limit_bytes"] != 4 * 1024**3
            for c in cases + checked["cases"]
        )
    ):
        raise RuntimeError("competing trials have different images/resource budgets")
    if any(
        c["snapshot"]["sha256"] != setup["expected_export"]["sha256"]
        or c["writes"]["acknowledged_rows"] != setup["mutation_rows"]
        for c in cases
    ):
        raise RuntimeError("snapshot/ACK oracle does not match")
    summaries = summarize(setup, cases)
    info = setup["docker_info"]
    output = {
        "format_version": 1,
        "run_id": benchmark_id,
        "date_utc": setup["date_utc"],
        "setup": {k: v for k, v in setup.items() if k != "docker_info"},
        "environment": {
            k: info[k]
            for k in (
                "Architecture",
                "NCPU",
                "MemTotal",
                "KernelVersion",
                "ServerVersion",
                "OperatingSystem",
            )
        },
        "correctness": {
            "run_id": correctness_id,
            "passed_cells": len(checked["cases"]),
            "new_rows": sum(c["writes"]["acknowledged_rows"] for c in checked["cases"]),
            "full_snapshot_rows": sum(
                c["snapshot"]["verified_rows"] for c in checked["cases"]
            ),
            "native_argument_rejections": sum(
                sum(
                    v["operations"]
                    for k, v in c["phases"].items()
                    if k in {"reject-append", "reject-columnar"}
                )
                for c in checked["cases"]
            ),
        },
        "summaries": summaries,
        "cases": cases,
        "controller": report["controller"],
        "limits": "Single-writer in-memory timing comparison, not sustained capacity or durability. Rayforce typed path uses public C vector/table/environment APIs plus constant Rayfall dispatch; DuckDB Appender paths flush each requested batch before ACK. DuckDB transaction/MVCC work remains enabled; Rayforce environment mutation does not establish equivalent SQL isolation. Prepared SQL is not evaluated here; measured paths are listed in setup.methods. Host gzip is verification-snapshot compressibility, not native engine compression. CPU/memory cover import, writes, probes and export; phase/window costs are separate. Docker daemon/VM overhead and controller average RSS are not measured.",
    }
    (bench.ROOT / filename).write_text(json.dumps(output, indent=2) + "\n")
    text = [
        "## " + heading,
        f"Measured **{setup['date_utc'][:10]}**, **{next(iter(cpus)):g} CPU / {setup['threads']} engine thread(s), 4 GiB per process**, the same pinned engines through Python/C++/Rust. Every fresh process loaded **{setup['fixture']['rows']:,} trades / 10,000 customers**, then appended **{setup['mutation_rows']:,}** deterministic six-I64 rows. **{len(cases)} process trials**, {setup['trials']} per cell; pair order reverses in the second trial.",
        f"`make test-ingestion METHODS={','.join(setup['methods'])} MUTATION_ROWS={checked_setup['mutation_rows']} BATCH_SIZES={','.join(map(str, checked_setup['batches']))}` runs correctness; `make ingestion-benchmark METHODS={','.join(setup['methods'])} MUTATION_ROWS={setup['mutation_rows']}` runs this matrix. Override comma-separated `BATCH_SIZES`, `METHODS` (`query,native,columnar`) and `LANGUAGES` (`python,cpp,rust`) explicitly. `TRIALS` is at least three for benchmarks. These targets reuse the example's normal five-command lifecycle and private one-shot Compose service; no second database server is involved.",
        f"Correctness passed **{len(checked['cases'])} cells**, **{output['correctness']['new_rows']:,} new rows**, **{output['correctness']['full_snapshot_rows']:,} complete snapshot rows** and **{output['correctness']['native_argument_rejections']} invalid native-call rejections**. Every timed cell also matched the SHA256 and byte count of *every* old and new row, in ID order. A changed old body is rejected even when row counts match. Point probes read the initial old row, middle-batch result and final new row.",
        "The [backend/client contract](ingestion-contract.json) declares same-process read-your-writes, no mutation retries/deduplication and **no crash durability**. Successful batches are query-visible in RAM. Query mode parses SQL INSERT VALUES or quoted-target Rayfall insert; typed mode uses DuckDB's row-wise C Appender with a flush per ACK, versus Rayforce C vectors/table/environment plus fixed Rayfall dispatch. Keeping a Rayforce view can disable its sole-owner fast path; this test retains no such view. A buffered Appender admission without flush would have a different ACK contract and is not used. DuckDB transaction/MVCC work remains enabled; Rayforce environment mutation does not establish equivalent SQL isolation. Prepared SQL remains unrun; this is not an exhaustive ranking of ingestion APIs.",
        "Rows/s below use the **actual first-write start to last-write finish**, including intervening plan handling/logging and the middle read when more than one request exists. They exclude CSV setup, initial/final probes, export and host verification. Request latency times the native call. Query payload strings are generated outside the container; typed timings include the shared C payload generator. These are bounded fixed-workload timings, not five-minute saturation/capacity tests or language implementation rankings.",
    ]
    if columnar:
        text.append(
            "`native` retains the row-wise Appender baseline; `columnar` reuses a six-BIGINT DuckDB data chunk, copying the same generated column-major values in native vector-sized pieces and flushing only after the entire requested batch. Rayforce uses its **identical typed-column implementation in both paths**: their differences estimate trial noise, not two Rayforce APIs. Chunk creation, reset, copies and flush are timed; this is not zero-copy ingestion. The [pinned collection implementation](https://github.com/duckdb/duckdb/blob/v2.0.0/src/common/types/column/column_data_collection.cpp) copies fixed-width values before the input chunk is reused. The correctness command above records its exact batch sizes and append count, including partial final batches when the count is not divisible by the batch size. The earlier query/row-wise [baseline](INGESTION_RESULTS.json) remains a separate immutable run; compare the two paths below using this run's common image and workload."
        )
    for method in setup["methods"]:
        rows = []
        for batch in setup["batches"]:
            row = [batch]
            for language in ("python", "cpp", "rust"):
                for engine in ("rayforce", "duckdb"):
                    cell = next(
                        s
                        for s in summaries
                        if (s["method"], s["batch_size"], s["language"], s["engine"])
                        == (method, batch, language, engine)
                    )
                    row.append(f"{cell['median_rows_per_second']:,.0f}")
            rows.append(row)
        text.extend(
            [
                "### "
                + (
                    "Parsed query inserts"
                    if method == "query"
                    else (
                        "Columnar values / flushed batches"
                        if method == "columnar"
                        else "Typed values / row-wise DuckDB Appender"
                    )
                ),
                f"Medians across {setup['trials']} fresh processes, rows/s.",
                table(
                    [
                        "Batch rows",
                        "Python Ray",
                        "Python Duck",
                        "C++ Ray",
                        "C++ Duck",
                        "Rust Ray",
                        "Rust Duck",
                    ],
                    rows,
                ),
            ]
        )
    rows = []
    costs = []
    for method in setup["methods"]:
        for batch in setup["batches"]:
            group = [
                next(
                    s
                    for s in summaries
                    if (s["method"], s["batch_size"], s["language"], s["engine"])
                    == (method, batch, "cpp", engine)
                )
                for engine in ("rayforce", "duckdb")
            ]
            rows.append(
                [
                    method,
                    batch,
                    *[
                        f"{s['median_rows_per_second']:,.0f} ({s['min_rows_per_second']:,.0f} … {s['max_rows_per_second']:,.0f}); IQR {s['iqr_rows_per_second']:,.0f}"
                        for s in group
                    ],
                    *[
                        f"{s['median_request_p50_ms']:.6f} / {s['median_request_p99_ms']:.6f}"
                        for s in group
                    ],
                ]
            )
            costs.append(
                [
                    method,
                    batch,
                    *[
                        f"{s['median_cpu_core_seconds']:.3f} / {s['median_ingestion_cpu_seconds']:.3f}"
                        for s in group
                    ],
                    *[
                        f"{s['median_charged_memory_mean_mib']:.1f} / {s['median_cgroup_peak_memory_mib']:.1f}"
                        for s in group
                    ],
                ]
            )
    text.extend(
        [
            "### C++ trial spread and call latency",
            f"Median (minimum … maximum) and inclusive IQR of measured rows/s; request p50/p99 in ms are medians of trial percentiles. Large batches have few requests; their p99 is near the maximum. All language trial/resource values remain in [{filename}]({filename}).",
            table(
                [
                    "Path",
                    "Batch",
                    "Ray rows/s",
                    "Duck rows/s",
                    "Ray p50/p99 ms",
                    "Duck p50/p99 ms",
                ],
                rows,
            ),
            "### Whole-process costs",
            "C++ medians: total container CPU / ingestion-window process CPU in core-seconds; sampled mean charged / native cgroup peak memory in MiB. Total CPU includes import, probes and full CSV export. Window CPU overlaps native write/probe phases: do not sum them twice. Resource samples run at roughly 100 ms; native RSS and detailed per-phase timings are in the JSON.",
            table(
                [
                    "Path",
                    "Batch",
                    "Ray CPU total/window",
                    "Duck CPU total/window",
                    "Ray memory mean/peak",
                    "Duck memory mean/peak",
                ],
                costs,
            ),
        ]
    )
    snapshot = cases[0]["snapshot"]
    text.extend(
        [
            f"Every final snapshot contains **{snapshot['verified_rows']:,} rows / {snapshot['bytes']:,} CSV bytes**. Each export's actual allocated bytes were recorded before removal; only fully verified exports are deleted, after fsynced proof metadata. Failed exports remain evidence. Raw plans, operation timing logs and samples are ignored under `results/ingestion/{benchmark_id}/`.",
            f"Off-path host gzip level 1 compresses that exact snapshot to **{snapshot['compression']['compressed_bytes']:,} bytes**, ratio **{snapshot['compression']['ratio']:.3f}×**; compression CPU/wall time is recorded separately. This measures dataset/export compressibility, **not native database storage compression**. Databases remain in memory; the owned writable bind mount holds verification witnesses only.",
            "The native path follows the pinned [DuckDB Appender implementation](https://github.com/duckdb/duckdb/blob/v2.0.0/src/main/capi/v1/appender-c.cpp), [Appender guidance](https://duckdb.org/docs/current/clients/c/appender) and [Rayforce insert implementation](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/ops/query.c).",
            "Read/write block bytes/IOPS and CPU utilization/native RSS/OOM events are retained per trial. Docker Desktop file sharing can leave container block counters at zero despite host export I/O; this is not a physical-disk comparison. No replication, independent concurrent readers, engine-OOM atomicity, process/disk recovery, persistent storage or OS-cold behavior is established.",
        ]
    )
    markdown = "\n\n".join(text) + "\n"
    path = bench.ROOT / "README.md"
    start, end = f"<!-- {section} -->", f"<!-- /{section} -->"
    existing = path.read_text()
    if start in existing:
        existing = (
            existing.split(start)[0]
            + start
            + "\n\n"
            + markdown
            + "\n"
            + end
            + existing.split(end, 1)[1]
        )
    else:
        anchor = (
            "## Analytical query outcome\n"
            if "## Analytical query outcome\n" in existing
            else "## Outcome\n"
        )
        if anchor not in existing:
            raise RuntimeError("README result insertion anchor is missing")
        existing = existing.replace(
            anchor,
            start
            + "\n\n"
            + markdown
            + "\n"
            + end
            + "\n\n## Analytical query outcome\n",
            1,
        )
    path.write_text(existing)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(
            "usage: write_ingestion_results.py BENCHMARK_RUN CORRECTNESS_RUN"
        )
    publish(*sys.argv[1:])
