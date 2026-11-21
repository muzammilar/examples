"""Render measured tables from two retained runs; no benchmark execution here."""

import json
from pathlib import Path
import statistics
import sys

ROOT = Path(__file__).resolve().parent


def table(headers, rows):
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]
        + ["| " + " | ".join(map(str, row)) + " |" for row in rows]
    )


def median(values):
    return statistics.median(list(values))


def render(runs):
    output = []
    resources, cpu_rows, first_rows, validation_rows, accounting, spreads = (
        [],
        [],
        [],
        [],
        [],
        [],
    )
    fingerprints = set()
    images = set()
    for run in runs:
        setup = json.loads((run / "setup.json").read_text())
        summary = json.loads((run / "summary.json").read_text())
        cases = [json.loads(p.read_text()) for p in run.glob("*-result.json")]
        threads = setup["threads"]
        fingerprints.add(json.dumps(setup["fixture"]["sha256"], sort_keys=True))
        images.update(case["image"] for case in cases)
        if any(
            case["cpus"] != threads or case["memory_limit_bytes"] != 4 * 1024**3
            for case in cases
        ):
            raise RuntimeError("report requires equal CPU/thread and four-GiB caps")
        if len(cases) != 18 or setup["trials"] != 3:
            raise RuntimeError("report requires all three trials for six combinations")
        output.append(
            f"### {threads} CPU / {threads} engine thread"
            + ("s" if threads != 1 else "")
        )
        rows = []
        for query in ["totals", "filtered", "symbols", "customers", "join"]:
            row = [query]
            for language in ["python", "cpp", "rust"]:
                for engine in ["rayforce", "duckdb"]:
                    x = next(
                        x
                        for x in summary
                        if x["language"] == language
                        and x["engine"] == engine
                        and x["query"] == query
                    )
                    row.append(f"{x['median_trial_p50_ms']:.3f}")
            rows.append(row)
        output.append(
            table(
                [
                    "Query",
                    "Python Ray ms",
                    "Python Duck ms",
                    "C++ Ray ms",
                    "C++ Duck ms",
                    "Rust Ray ms",
                    "Rust Duck ms",
                ],
                rows,
            )
        )
        for language in ["python", "cpp", "rust"]:
            for engine in ["rayforce", "duckdb"]:
                group = [
                    x
                    for x in cases
                    if x["language"] == language and x["engine"] == engine
                ]
                load_ms = median(
                    next(
                        r["elapsed_ns"] / 1e6
                        for r in x["records"]
                        if r["phase"] == "load" and r["query"] == "load0"
                    )
                    for x in group
                )
                resources.append(
                    [
                        threads,
                        language,
                        engine,
                        f"{load_ms:.2f}",
                        f"{setup['fixture']['rows'] / (load_ms / 1000) / 1e6:.2f}",
                        f"{median(x['cgroup_cpu_seconds'] for x in group):.3f}",
                        f"{median(x['process_peak_rss_kib'] / 1024 for x in group):.1f}",
                        f"{median(x['cgroup_peak_memory_bytes'] / 1048576 for x in group):.1f}",
                    ]
                )
                if any(not x["sampled_resources"]["available"] for x in group):
                    raise RuntimeError(
                        "resource sampling missing from a reported trial"
                    )
                io_available = all(x["block_io"]["available"] for x in group)
                accounting.append(
                    [
                        threads,
                        language,
                        engine,
                        f"{median(x['cpu_utilization_percent_one_core_100'] for x in group):.1f}",
                        f"{median(x['sampled_resources']['sampled_peak_cpu_percent_one_core_100'] for x in group):.1f}",
                        f"{median(x['sampled_resources']['sampled_mean_charged_memory_bytes'] / 1048576 for x in group):.1f}",
                        f"{median(x['sampled_resources']['sampled_max_charged_memory_bytes'] / 1048576 for x in group):.1f}",
                        *[
                            f"{median(x['block_io']['totals'][key] for x in group):.0f}"
                            if io_available
                            else "unavailable"
                            for key in ("rbytes", "wbytes")
                        ],
                        *[
                            f"{median(x['block_io']['per_second'][key] for x in group):.2f}"
                            if io_available
                            else "unavailable"
                            for key in ("rios", "wios")
                        ],
                    ]
                )
        for query in ["totals", "filtered", "symbols", "customers", "join"]:
            row = [threads, query]
            first = [threads, query]
            for engine in ["rayforce", "duckdb"]:
                x = next(
                    x
                    for x in summary
                    if x["language"] == "cpp"
                    and x["engine"] == engine
                    and x["query"] == query
                )
                row.append(f"{median(v['mean_cpu_ms'] for v in x['trials']):.3f}")
                group = [
                    x for x in cases if x["language"] == "cpp" and x["engine"] == engine
                ]
                first.append(
                    f"{median(r['elapsed_ns'] / 1e6 for x in group for r in x['records'] if r['phase'] == 'first' and r['query'] == query):.3f}"
                )
            cpu_rows.append(row)
            first_rows.append(first)
            spread = [threads, query]
            for engine in ("rayforce", "duckdb"):
                cell = next(
                    x
                    for x in summary
                    if x["language"] == "cpp"
                    and x["engine"] == engine
                    and x["query"] == query
                )
                values = sorted(t["p50_ms"] for t in cell["trials"])
                quartiles = statistics.quantiles(values, n=4, method="inclusive")
                spread.extend(
                    [
                        f"{median(values):.3f} ({min(values):.3f} … {max(values):.3f})",
                        f"{quartiles[2] - quartiles[0]:.3f}",
                    ]
                )
            spreads.append(spread)
        validation_rows.append(
            [
                run.name,
                threads,
                len(cases),
                sum(len(x["records"]) - 2 for x in cases),
                min(x["cpus"] for x in cases),
                max(x["memory_limit_bytes"] for x in cases) // 1048576,
            ]
        )
    output += [
        "### Resource costs",
        "Medians of three process runs. CSV timing covers the 2-million-row trade import; M rows/s uses that duration. Container CPU includes both imports, all 95 checked queries, runtime startup/shutdown and telemetry commands. RSS is the process high-water mark; cgroup peak also includes charged file cache/kernel memory and wrapper processes. These are whole-run peaks, not per-query retained heap sizes.",
        table(
            [
                "CPUs",
                "Language",
                "Engine",
                "CSV ms",
                "M rows/s",
                "CPU core-s",
                "Peak RSS MiB",
                "Cgroup peak MiB",
            ],
            resources,
        ),
        "### CPU utilization, sampled memory and block I/O",
        "Medians of three process trials. One CPU core is 100%; four cores can approach 400%. CPU average uses cumulative cgroup CPU divided by the actual container lifetime, not Docker polling duration. CPU peaks and charged-memory means/maxima come from approximately 100 ms API observations; means are arithmetic sample means. Native cgroup peak and process RSS above remain separate counters. Raw per-device io.stat is retained. Read/write IOPS divide exported operation counts by container lifetime; these are not logical CSV or engine reads.",
        table(
            [
                "CPUs",
                "Language",
                "Engine",
                "CPU avg %",
                "Sample CPU peak %",
                "Mean charged MiB",
                "Sample max MiB",
                "Read bytes",
                "Write bytes",
                "Read IOPS",
                "Write IOPS",
            ],
            accounting,
        ),
        "Zero exported block counters describe this cached, in-memory run; Docker Desktop file sharing can do host I/O outside the container cgroup. They do not establish zero physical disk activity or disk-performance parity. No native database files are persisted, so native storage compression is not applicable to this profile. Input CSV logical/host-allocated bytes are recorded in setup.json and BENCHMARK_RESULTS.json; /tmp is memory-backed. Device I/O latency and queue depth were not exposed.",
        "### Successful-query CPU time",
        "C++ representative: median of each trial’s mean process CPU milliseconds per warm query, including native result conversion. CPU time sums all engine threads and may exceed wall latency. Python/Rust have the same raw counters in their result files.",
        table(["CPUs", "Query", "Ray CPU ms", "Duck CPU ms"], cpu_rows),
        "### Trial variability",
        "C++ per-trial p50 milliseconds: median (minimum … maximum), plus inclusive IQR across three fresh processes. These are trial-median spreads, not pooled request tails or confidence intervals. Python/Rust trial values and spreads are retained in BENCHMARK_RESULTS.json.",
        table(
            [
                "CPUs",
                "Query",
                "Ray median (min … max) ms",
                "Ray IQR ms",
                "Duck median (min … max) ms",
                "Duck IQR ms",
            ],
            spreads,
        ),
        "### First query after fresh import",
        "C++ medians across three fresh processes. Each query appears once in the fixed order shown, after CSV loading. This is **not an OS-cold-cache measurement**: loading and earlier queries have already touched pages.",
        table(["CPUs", "Query", "Ray first ms", "Duck first ms"], first_rows),
        "### Evidence",
        table(
            [
                "Run ID",
                "Threads",
                "Processes",
                "Checked queries",
                "CPU cap",
                "Memory cap MiB",
            ],
            validation_rows,
        ),
    ]
    if len(fingerprints) != 1 or len(images) != 1:
        raise RuntimeError("comparison inputs and executable image must be identical")
    return "\n\n".join(output) + "\n"


def publish_json(runs):
    profiles = []
    for run in runs:
        setup = json.loads((run / "setup.json").read_text())
        info = setup["docker_info"]
        cases = [json.loads(p.read_text()) for p in sorted(run.glob("*-result.json"))]
        summaries = json.loads((run / "summary.json").read_text())
        for cell in summaries:
            values = sorted(t["p50_ms"] for t in cell["trials"])
            quartiles = statistics.quantiles(values, n=4, method="inclusive")
            cell.update(
                min_trial_p50_ms=min(values),
                max_trial_p50_ms=max(values),
                iqr_trial_p50_ms=quartiles[2] - quartiles[0],
            )
        profiles.append(
            {
                "run_id": run.name,
                "date_utc": setup["date_utc"],
                "threads": setup["threads"],
                "repeats": setup["repeats"],
                "trials": setup["trials"],
                "fixture": setup["fixture"],
                "environment": {
                    key: info[key]
                    for key in (
                        "Architecture",
                        "NCPU",
                        "MemTotal",
                        "KernelVersion",
                        "ServerVersion",
                        "OperatingSystem",
                    )
                },
                "storage": setup["storage"],
                "sources": setup["sources"],
                "source_sha256": setup["source_sha256"],
                "cases": [
                    {k: v for k, v in case.items() if k != "records"} for case in cases
                ],
                "query_summary": summaries,
                "controller": json.loads(
                    (run / "controller-resources.json").read_text()
                ),
            }
        )
    (ROOT / "BENCHMARK_RESULTS.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "profiles": profiles,
                "scope": "Same native engines and C ABI in Python, C++ and Rust; two million seeded trades and ten thousand customers. Three fresh processes per cell; warm 15-query samples, not sustained capacity, OS-cold storage, persistence or native-language implementation comparisons.",
                "limits": "Cgroup I/O can omit host-side Docker file sharing. Sample means/peaks can miss fast transitions; native cgroup/RSS peaks are separate. Native storage compression is not applicable: both engines are in memory and /tmp is tmpfs. Host controller CPU and peak RSS are separate; controller average RSS/time series and Docker daemon/VM costs are not collected.",
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    runs = [ROOT / "results/runs" / name for name in sys.argv[1:]]
    if len(runs) != 2:
        raise SystemExit("usage: write_results.py ONE_CPU_RUN FOUR_CPU_RUN")
    path = ROOT / "README.md"
    marker = "<!-- measured-results -->"
    text = path.read_text().split(marker)[0]
    path.write_text(text + marker + "\n\n" + render(runs))
    publish_json(runs)
