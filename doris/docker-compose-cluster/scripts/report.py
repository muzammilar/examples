"""Curate bounded functional/recovery evidence; do not rank performance."""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def read(path):
    return json.loads((RESULTS / path).read_text())


def counter(stats, operation):
    values = stats.get("blkio_stats", {}).get("io_service_bytes_recursive") or []
    return sum(v["value"] for v in values if v["op"].lower() == operation)


def resources(path):
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    header = lines[0]
    samples = [line for line in lines[1:] if line.get("type") == "resources"]
    previous = {}
    cpu_ns = disk_read = disk_write = rx = tx = peak_memory = 0
    observed = set()
    for sample in samples:
        memory = 0
        for actor in sample["actors"]:
            stats = actor.get("counters")
            if not stats or not stats.get("cpu_stats"):
                continue
            observed.update(actor["names"])
            memory += stats.get("memory_stats", {}).get("usage", 0)
            now = {"cpu": stats["cpu_stats"]["cpu_usage"]["total_usage"],
                   "read": counter(stats, "read"), "write": counter(stats, "write"),
                   "rx": sum(n["rx_bytes"] for n in (stats.get("networks") or {}).values()),
                   "tx": sum(n["tx_bytes"] for n in (stats.get("networks") or {}).values())}
            old = previous.get(actor["id"])
            if old:
                # Never subtract reset counters or invent unobserved startup work.
                cpu_ns += max(0, now["cpu"] - old["cpu"])
                disk_read += max(0, now["read"] - old["read"])
                disk_write += max(0, now["write"] - old["write"])
                rx += max(0, now["rx"] - old["rx"])
                tx += max(0, now["tx"] - old["tx"])
            previous[actor["id"]] = now
        peak_memory = max(peak_memory, memory)
    duration = samples[-1]["elapsed_s"] - samples[0]["elapsed_s"] if len(samples) > 1 else 0
    return {"command": header["command"], "history": str(path.relative_to(ROOT)),
            "environment": header["environment"], "samples": len(samples),
            "observation_s": duration,
            "average_cpu_pct_of_one_core_lower_bound": cpu_ns / 1e9 / duration * 100 if duration else None, "sampled_actors": sorted(observed),
            "sampled_total_memory_peak_bytes_including_page_cache": peak_memory,
            "observed_deltas_lower_bound": {"cpu_core_seconds": cpu_ns / 1e9,
                "block_read_bytes": disk_read, "block_write_bytes": disk_write,
                "network_receive_bytes": rx, "network_transmit_bytes": tx},
            "observed_block_read_bytes_per_s_lower_bound": disk_read / duration if duration else None,
            "observed_block_write_bytes_per_s_lower_bound": disk_write / duration if duration else None}


def main():
    recoveries = sorted(RESULTS.glob("backend-recovery-*/completed.json"))
    complete = recoveries[-1].parent
    native = read("native-topology.json")
    final = read("final-storage-cgroups.json")
    storage = {}
    for role, raw in final.items():
        if role.endswith("_config"):
            continue
        storage[role] = {"allocated_role_storage_bytes": int(raw.splitlines()[0].split()[0]),
                         "lifetime_block_counters_since_current_container_start": {
                             key: sum(map(int, re.findall(r"\b" + key + r"=(\d+)", raw)))
                             for key in ("rbytes", "wbytes", "rios", "wios")}}
    sources = ["docker-compose.yml", "orders-workload/main.go", "docker/Dockerfile.fe", "docker/Dockerfile.be"]
    attempts = [json.loads(line) for line in (complete / "read-attempts.jsonl").read_text().splitlines()]
    first_failure = next(attempt for attempt in attempts if attempt["code"] != 0)
    table_lines = (RESULTS / "native-table-storage.txt").read_text().splitlines()
    table_header = next(i for i, line in enumerate(table_lines) if line.startswith("TableName\tIndexName\tSize"))
    table_size = table_lines[table_header + 1].split("\t")[2]
    report = {
        "date": "2026-11-26", "classification": "functional-recovery-diagnostics",
        "engine_versions": sorted({b["Version"] for b in native["backends"]}),
        "topology": read("topology-verdict.json"),
        "revision_1_all_replica_reads": [read(f"replica-{i}-revision-1.json") for i in range(3)],
        "revision_2_all_replica_reads": [read(f"replica-{i}-revision-2.json") for i in range(3)],
        "strict_immediate_outage_query": {"status": "FAILED", "error": "RpcException: send fragments failed; UNAVAILABLE, dead backend 172.30.42.21",
                                          "first_error_duration_s_in_followup": first_failure["duration_s"],
                                          "evidence": "results/failed-immediate-backend-read/"},
        "bounded_recovery": json.loads((complete / "outage-receipt.json").read_text()),
        "restored_replica_verification": json.loads((complete / "completed.json").read_text()),
        "storage": storage,
        "native_effective_backend_config": {k: v for k, v in final.items() if k.endswith("_config")},
        "native_table_size_display_after_revision2": table_size + " across 24 replica records; asynchronous metadata observation",
        "native_compression_ratio": None,
        "resource_phases": [resources(p) for p in sorted(RESULTS.glob("resources-*.jsonl"))],
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
        "limitations": [
            "No performance comparison or capacity qualification. Level-3 competitive trials are pending.",
            "Raw counters miss work before/after actor samples; short-lived Go actors may not have two samples. All deltas are observed lower bounds.",
            "A restarted backend resets counters. Final lifetime I/O-operation counts are not phase-specific IOPS.",
            "Docker raw stats expose byte counters but not I/O-operation counts or device latency on this host; final native cgroup snapshots supply lifetime operation counts only.",
            "Storage is one point after an update; includes internal tables, obsolete versions and temporary/native files. Not settled compaction space or a codec compression ratio.",
            "Network aggregation counts inter-role traffic at send and receive endpoints; do not interpret both as external ingress.",
            "Quiesced committed-data recovery only; no live producer or durable per-record ACK journal.",
            "Single physical host; permanent disk loss, partitions, quorum loss, FE leadership failover, independent restore and operator coverage remain pending.",
            "Initial topology controller used LastFailedVersion instead of released LstFailedVersion. It failed after three body checks passed; preserved data was rechecked after correction."
        ],
    }
    (ROOT / "reports/INITIAL_RESULTS.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"report": "reports/INITIAL_RESULTS.json", "storage_bytes": sum(v["allocated_role_storage_bytes"] for v in storage.values())}))


if __name__ == "__main__":
    main()
