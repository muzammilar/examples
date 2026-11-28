"""Keep healthy controls, failed attempts and measurement limits reviewable."""

import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text())


def records(path):
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.startswith("{")
    ]


def latency(values):
    ordered = sorted(values)
    if not ordered:
        return {"samples": 0}
    return {
        "samples": len(ordered),
        "unit": "ms",
        "mean": sum(ordered) / len(ordered),
        **{
            key: ordered[min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1)]
            for key, q in [
                ("p50", 0.5),
                ("p95", 0.95),
                ("p99", 0.99),
                ("p999", 0.999),
                ("max", 1),
            ]
        },
        "qualification": "One finite-corpus diagnostic; not sustained/repeated performance qualification",
    }


def main():
    cases = []
    selected = {}
    for path in sorted(
        (ROOT / "results/cases").glob("*/case.json"), key=lambda p: p.stat().st_mtime_ns
    ):
        metadata = load(path)
        if metadata["kind"] not in [
            "driver-control",
            "healthy-common-otlp",
            "healthy-native",
        ]:
            continue
        status_path = path.parent / "status.json"
        status = (
            load(status_path)
            if status_path.exists()
            else {"passed": False, "state": "incomplete"}
        )
        fixture = load(path.parent / "fixture.json")
        signals = {}
        for backend in ["greptime", "victoria"]:
            for signal in ["metrics", "logs"]:
                directory = path.parent / backend / signal
                journal = directory / "requests.jsonl"
                if not journal.exists():
                    continue
                responses = [r for r in records(journal) if r["event"] == "response"]
                checks = sorted(
                    directory.glob("verify-*.log"),
                    key=lambda p: int(p.stem.split("-")[1]),
                )
                exact = [
                    r
                    for check in checks
                    for r in records(check)
                    if r.get("event") == "exact-readback"
                ]
                raw_bytes = sum(r["raw_bytes"] for r in responses)
                wire_bytes = sum(r["wire_bytes"] for r in responses)
                signals[backend + ":" + signal] = {
                    "configuration": load(directory / "configuration.json"),
                    "request_responses": len(responses),
                    "accepted_records": sum(
                        r["last_exclusive"] - r["first"]
                        for r in responses
                        if r["outcome"] == "accepted"
                    ),
                    "unaccepted_responses": sum(
                        r["outcome"] != "accepted" for r in responses
                    ),
                    "http_latency": latency([r["http_ms"] for r in responses]),
                    "request_construction_encoding_latency": latency(
                        [r["encoding_ms"] for r in responses]
                    ),
                    "raw_encoded_bytes": raw_bytes,
                    "wire_bytes": wire_bytes,
                    "wire_compression_ratio": (
                        raw_bytes / wire_bytes if wire_bytes else None
                    ),
                    "last_exact_readback": (
                        exact[-1] if exact else {"state": "not passed in this attempt"}
                    ),
                    "admission": [
                        r
                        for r in records(directory / "push.log")
                        if r.get("event") == "admission-complete"
                    ],
                    "evidence": str(directory.relative_to(ROOT)),
                }
        entry = {
            "case": metadata,
            "fixture": fixture,
            "status": status,
            "signals": signals,
            "executed_workload_source": (
                load(path.parent / "source-manifest.json")
                if (path.parent / "source-manifest.json").exists()
                else None
            ),
            "data_snapshots": {
                p.name: load(p) for p in sorted(path.parent.glob("*-disk-*.json"))
            },
        }
        cases.append(entry)
        protocol = metadata.get("protocol", "otlp")
        if (
            fixture["series"] * fixture["points"] == 1000000
            and fixture["logs"] == 1000000
        ):
            selected[protocol] = (path, status_path, entry)
    if set(selected) != {"otlp", "native"} or any(
        not entry["status"]["passed"] for _, _, entry in selected.values()
    ):
        raise RuntimeError(
            "Latest million-record controls must pass for both protocols"
        )
    streams = []
    for path in sorted((ROOT / "results").glob("resources-*.jsonl")):
        with path.open() as source:
            header = json.loads(next(source))
        streams.append((path, header))
    resource_windows = []
    for protocol, (case, status, _) in selected.items():
        eligible = [
            (p, h)
            for p, h in streams
            if h["run_id"] <= case.stat().st_mtime_ns
            and p.stat().st_mtime_ns + 5_000_000_000 >= status.stat().st_mtime_ns
        ]
        if not eligible:
            raise RuntimeError("Missing observed resource coverage for " + protocol)
        path, _ = max(eligible, key=lambda pair: pair[1]["run_id"])
        summary = json.loads(
            subprocess.check_output(
                [sys.executable, str(ROOT / "scripts/resource_report.py"), str(path)],
                text=True,
            )
        )
        resource_windows.append(
            {
                "protocol": protocol,
                "scope": "Entire Make invocation including retained-data preflight, backend transitions, ingestion and full readback; not isolated per-backend performance cost",
                "summary": summary,
            }
        )
    report = {
        "scope": "Pass 1 driver/healthy ingestion and lifecycle controls only; no performance/recovery winner",
        "hardware": load(ROOT / "results/docker-environment.json"),
        "hardware_augmentation": (
            load(ROOT / "results/hardware-augmentation.json")
            if (ROOT / "results/hardware-augmentation.json").exists()
            else {"host_cpu_model": "unavailable"}
        ),
        "backend_budget": {
            "cpu": 4,
            "memory_bytes": 4 * 2**30,
            "greptime": {"cpu": 4, "memory_bytes": 4 * 2**30},
            "victoria_each_of_two": {"cpu": 2, "memory_bytes": 2 * 2**30},
        },
        "cases": cases,
        "resource_windows": resource_windows,
        "verified_teardowns": [
            load(p) for p in sorted((ROOT / "results/teardowns").glob("*/status.json"))
        ],
        "findings": [
            "VictoriaMetrics adds scope.name and maps omitted scope.version to unknown. Initial strict metadata expectations failed; corrected independent readback verified already accepted data before another corpus was emitted.",
            "The first native driver status description inherited an OTLP template. Its case/configuration protocol is native; classify by the executed configuration and source manifest, not that descriptive template.",
            "Native JSON logs use different framing: a JSON array through the checked-in Greptime pipeline and NDJSON through VictoriaLogs. Bodies/timestamps are the same logical input; Remote Write uses official Prometheus protobufs and Snappy.",
            "Synchronous Greptime WAL and periodically flushed Victoria buffers are different acknowledgement/durability contracts. Healthy visibility and graceful-stop preservation do not qualify abrupt-crash durability.",
        ],
        "measurement_limits": [
            "These finite-corpus runs were not the repeated five-minute performance cells. No throughput, resource-efficiency, codec or durability winner is qualified.",
            "Dataset construction/encoding scope changed while building the controls; old OTLP encoding time excludes corpus-object construction. Source manifests preserve that difference. API round trips and whole-loop diagnostics remain separate.",
            "All batch-1000 latency samples are request latencies, not measured single-record-operation latencies. Batch-size screens, cold/warm analytical queries and repeated trials remain pending.",
            "Disk snapshots include cumulative owned generation state, WAL/indexes/metadata and prior controls; differing prior attempts and compaction state prevent normalized cross-backend storage conclusions. Images/overlay/engine logs and raw host evidence are excluded.",
            "Wire compression is measured from raw encoded/wire request bytes. Native on-disk codec uncompressed/compressed byte ratios are not collected; do not equate volume allocation or protocol ratios with a native codec ratio.",
            "Docker samples may miss short-lived tools; native pre-exit cgroup records are retained in tool logs. Missing disk IOPS/device latency/queue depth are unavailable, not zero. Host controller/observer overhead is outside container CPU counters.",
            "First-generation setup predates its resource observer. Early inherited phase labels were later made explicit/atomic. Aggregate test-window counters are diagnostic, not accurate startup/phase performance attribution.",
        ],
        "next": [
            "live generator/reader during abrupt process loss with ACK/ambiguity/loss reconciliation",
            "matched sustained and repeated batch/native comparisons after recovery gates",
            "engine-cold/warm query oracles and timing",
            "comparable cluster contract/topology profile",
            "three-signal and shared incident-stack comparisons",
        ],
    }
    destination = ROOT / "reports/BASELINE.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(destination.relative_to(ROOT))


if __name__ == "__main__":
    main()
