"""Compact frozen-case facts; raw histories stay under ignored results/."""

import collections
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def summarize(case):
    result = {
        "case": json.loads((case / "case.json").read_text()),
        "fixture": json.loads((case / "fixture.json").read_text()),
        "scope": "Paced functional/recovery diagnostics, not repeated capacity benchmarks",
        "backends": {},
    }
    for backend in ["greptime", "victoria", "grafana"]:
        directory = case / backend
        if not (directory / "fenced.json").exists():
            continue
        item = {
            "fence": json.loads((directory / "fenced.json").read_text()),
            "publication": rows(directory / "incident-publisher.log"),
            "signals": {},
        }
        for signal in ["metrics", "logs", "traces"]:
            responses = [
                r
                for r in rows(directory / signal / "requests.jsonl")
                if r["event"] == "response"
            ]
            latencies = sorted(r["http_ms"] for r in responses if "http_ms" in r)
            raw = sum(r["raw_bytes"] for r in responses)
            wire = sum(r["wire_bytes"] for r in responses)
            item["signals"][signal] = {
                "requests": len(responses),
                "outcomes": dict(collections.Counter(r["outcome"] for r in responses)),
                "source_protobuf_bytes": raw,
                "source_gzip_bytes": wire,
                "wire_compression_ratio": raw / wire if wire else None,
                "request_http_latency_ms": {
                    label: (
                        latencies[
                            min(len(latencies) - 1, math.ceil(q * len(latencies)) - 1)
                        ]
                        if latencies
                        else None
                    )
                    for label, q in [
                        ("p50", 0.5),
                        ("p95", 0.95),
                        ("p99", 0.99),
                        ("p99_9", 0.999),
                        ("max", 1),
                    ]
                },
            }
        item["storage"] = {}
        for label in ["before-ingest", "after-actors"]:
            snapshot = case / (backend + "-disk-" + label + ".json")
            if snapshot.exists():
                raw = json.loads(snapshot.read_text())
                item["storage"][label] = {
                    "allocated_data_bytes": raw["allocated_data_bytes"],
                    "logical_file_bytes": raw["logical_file_bytes"],
                    "scope": "Includes retained prior fixtures; point snapshot, not transient peak or codec compression",
                }
        fault = case / (backend + "-collector-killed.json")
        recovery = case / (backend + "-collector-recovery.json")
        if fault.exists():
            native = json.loads(fault.read_text())
            item["collector_fault"] = {
                "id": native["Id"],
                "image": native["Image"],
                "state": native["State"],
                "mounts": native["Mounts"],
            }
        if recovery.exists():
            item["collector_recovery"] = json.loads(recovery.read_text())
        reads = rows(directory / "reader/reads.jsonl")
        item["live_read_outcomes"] = dict(
            collections.Counter(
                r["signal"] + ":" + r["kind"] + ":" + r["result"] for r in reads
            )
        )
        item["visible_recent_ack_age_ms"] = {}
        for signal in ["metrics", "logs", "traces"]:
            ages = [
                r["sampled_ack_age_ms"]
                for r in reads
                if r["signal"] == signal
                and r["kind"] == "recent"
                and r["result"] in ["visible", "fidelity-mismatch"]
            ]
            item["visible_recent_ack_age_ms"][signal] = {
                "samples": len(ages),
                "min": min(ages) if ages else None,
                "max": max(ages) if ages else None,
            }
        item["frozen_readbacks"] = [
            {"path": str(p.relative_to(ROOT)), "report": json.loads(p.read_text())}
            for p in sorted(directory.glob("*/readback-*.json"))
        ]
        result["backends"][backend] = item
    result["limitations"] = [
        "Request latencies include collector admission, not backend durable commit or individual-record latency.",
        "Empirical p99.9 from 1,000 requests is near the observed maximum, not established long-tail behavior.",
        "Wire gzip ratio is not on-disk codec compression.",
        "Recent read observations sample selected ACKs; they are not a complete visibility-latency distribution.",
        "Use accompanying resource summaries and volume snapshots for CPU, memory, disk I/O, storage and network; missing device metrics remain unavailable.",
    ]
    return result


def main():
    identifier = sys.argv[1]
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid case ID")
    print(json.dumps(summarize(ROOT / "results/cases" / identifier), indent=2))


if __name__ == "__main__":
    main()
