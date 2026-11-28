"""Curate every run, including rejected probes; raw files and archives stay ignored."""

import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
import lab


def ms(value):
    return (
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000
    )


def timing(values):
    if not values:
        return None
    values = sorted(values)
    return dict(
        n=len(values),
        mean_ms=statistics.mean(values),
        p50_ms=statistics.median(values),
        p95_ms=values[math.ceil(0.95 * len(values)) - 1],
        p99_ms=values[math.ceil(0.99 * len(values)) - 1],
        max_ms=max(values),
        p999_ms=None,
        note="Nearest-rank percentiles; small samples do not qualify tails; maximum retained",
    )


def counters(actor):
    row = actor["counters"]
    result = dict(cpu_ns=row["cpu_stats"]["cpu_usage"]["total_usage"])
    for item in row.get("blkio_stats", {}).get("io_service_bytes_recursive") or []:
        key = item["op"].lower()
        if key in ("read", "write"):
            result[key + "_bytes"] = result.get(key + "_bytes", 0) + item["value"]
    for interface in (row.get("networks") or {}).values():
        for key in ("rx_bytes", "tx_bytes"):
            result[key] = result.get(key, 0) + interface[key]
    return result


def resources(folder):
    file = next(folder.glob("resources-*.jsonl"))
    rows = [json.loads(line) for line in file.read_text().splitlines()]
    environment = rows[0]["environment"]
    epochs, roles, last, totals = {}, {}, {}, {}
    charged, working, cpu = [], [], []
    errors = 0
    for sample in rows[1:]:
        mem = work = util = 0
        measured = False
        for actor in sample.get("actors", []):
            if "collector_error" in actor or "counters" not in actor:
                errors += 1
                continue
            config = actor["configuration"]
            if not actor["counters"].get("cpu_stats", {}).get("cpu_usage") or not actor[
                "counters"
            ].get("read"):
                errors += 1
                continue
            role = (
                config.get("role")
                or actor["names"][0]
                .removeprefix("/" + lab.PROJECT + "-")
                .rsplit("-", 1)[0]
            )
            key = (actor["id"], config["started_at"])
            epochs.setdefault(role, set()).add(key)
            roles.setdefault(
                role,
                dict(cpu_ns=0, read_bytes=0, write_bytes=0, rx_bytes=0, tx_bytes=0),
            )
            current = counters(actor)
            at = ms(actor["counters"]["read"])
            previous = last.get(key)
            if previous:
                old_at, old = previous
                delta = {
                    k: v - old[k]
                    for k, v in current.items()
                    if k in old and v >= old[k]
                }
                for name, value in delta.items():
                    roles[role][name] = roles[role].get(name, 0) + value
                    totals[name] = totals.get(name, 0) + value
                if at - old_at >= 10:
                    util += delta.get("cpu_ns", 0) / ((at - old_at) * 1e6) * 100
            last[key] = (at, current)
            memory = actor["counters"].get("memory_stats") or {}
            mem += memory.get("usage", 0)
            work += max(
                0,
                memory.get("usage", 0)
                - memory.get("stats", {}).get("inactive_file", 0),
            )
            measured = True
        if measured:
            charged.append(mem)
            working.append(work)
            cpu.append(util)
    return dict(
        environment=environment,
        observed_native_deltas=totals,
        per_role_observed_deltas=roles,
        epochs_per_role={k: len(v) for k, v in epochs.items()},
        sampled_charged_memory_peak_bytes=max(charged) if charged else None,
        sampled_charged_memory_mean_bytes=statistics.mean(charged) if charged else None,
        sampled_working_set_peak_bytes=max(working) if working else None,
        sampled_cpu_mean_percent_one_core_100=statistics.mean(cpu) if cpu else None,
        sampled_cpu_peak_percent_one_core_100=max(cpu) if cpu else None,
        samples=len(charged),
        collector_errors=errors,
        limitation="First/last sampling tails and short-lived actors can be missed. Epochs are never subtracted across restart. Tool lifetime counters are separate and overlap sampled data; do not add them.",
        disk_iops_latency="Docker API does not expose native service counts/latency on this VM; unavailable",
    )


def main():
    phases = []
    for folder in sorted((lab.ROOT / "results").iterdir()):
        if not folder.is_dir() or not (folder / "status.json").exists():
            continue
        row = json.loads((folder / "status.json").read_text())
        row["raw_path"] = str(folder.relative_to(lab.ROOT))
        row["wall_s"] = (
            (row["ended_ns"] - row["started_ns"]) / 1e9 if "ended_ns" in row else None
        )
        if (folder / "fixture.json").exists():
            row["fixture_id"] = json.loads((folder / "fixture.json").read_text())["id"]
        if list(folder.glob("resources-*.jsonl")):
            row["resources"] = resources(folder)
        if (folder / "source-sha256.json").exists():
            row["source_manifest_sha256"] = hashlib.sha256(
                (folder / "source-sha256.json").read_bytes()
            ).hexdigest()
        for name in (
            "verification",
            "recovery",
            "locks",
            "logical-copy",
            "remnants",
            "stopped-state",
        ):
            file = folder / (name + ".json")
            if file.exists():
                row[name] = json.loads(file.read_text())
        killed = folder / "fault-killed.json"
        restored = folder / "fault-restored.json"
        if killed.exists():
            dead = json.loads(killed.read_text())
            row["fault"] = dict(
                role=dead["Config"]["Labels"]["com.docker.compose.service"],
                finished_at=dead["State"]["FinishedAt"],
                exit_code=dead["State"]["ExitCode"],
                oom=dead["State"]["OOMKilled"],
            )
            if restored.exists():
                live = json.loads(restored.read_text())
                row["fault"].update(
                    started_at=live["State"]["StartedAt"],
                    process_down_ms=ms(live["State"]["StartedAt"])
                    - ms(dead["State"]["FinishedAt"]),
                )
        publications, ambiguous, reads, cycles, failed_cycles, processes, tools = (
            [],
            [],
            [],
            [],
            [],
            [],
            [],
        )
        read_groups = {}
        ack_ends = []
        for file in folder.glob("*.log"):
            events = lab.records(file, strict=False)
            role = next(
                (
                    name
                    for name in ("files-writer", "files-reader", "cold-reader")
                    if file.name.startswith(name + "-")
                ),
                None,
            )
            mode = (
                "historical-live-cycle"
                if any(
                    e.get("event") in ("read_cycle", "read_cycle_fail") for e in events
                )
                else "one-shot-readback"
            )
            for event in events:
                kind = event.get("event")
                if kind == "ack":
                    publications.append(event["duration_ms"])
                    ack_ends.append(event["utc_end_ms"])
                if kind == "ambiguous":
                    ambiguous.append(
                        dict(
                            sequence=event["sequence"],
                            run=event["run"],
                            error=event["error"],
                            duration_ms=event["duration_ms"],
                        )
                    )
                if kind == "body_read":
                    reads.append(event["duration_ms"])
                    read_groups.setdefault(str(role) + ":" + mode, []).append(
                        event["duration_ms"]
                    )
                if kind == "read_cycle":
                    cycles.append(event["duration_ms"])
                if kind == "read_cycle_fail":
                    failed_cycles.append(event)
                if kind == "resources":
                    processes.append(event)
                if "tool_counters" in event:
                    tools.append(event["tool_counters"])
        row["write_success"] = timing(publications)
        row["ambiguous_publications"] = ambiguous
        row["read_success_mixed_diagnostics_only"] = timing(reads)
        row["read_success_by_role_and_approach"] = {
            k: timing(v) for k, v in read_groups.items()
        }
        row["largest_ack_completion_gap_ms"] = max(
            (b - a for a, b in zip(ack_ends, ack_ends[1:])), default=None
        )
        row["complete_historical_read_cycles"] = timing(cycles)
        row["failed_read_cycles"] = failed_cycles
        row["process_resources"] = processes
        row["one_shot_lifetime_counters"] = tools
        volumes = sorted(folder.glob("volumes-*.json"))
        if volumes:
            row["volume_samples"] = [
                {
                    k: v
                    for k, v in json.loads(f.read_text()).items()
                    if not k.startswith("_")
                }
                for f in volumes
            ]
        probe = folder / "cold-probe.json"
        if probe.exists():
            data = json.loads(probe.read_text())
            events = data["rows"]
            row["cold_probe"] = dict(
                events=events,
                observation_scope="Read join/collection occurs after restore; use native event timestamps for actual completion",
            )
        phases.append(row)
    lab.save(
        lab.ROOT / "reports/QUALIFICATION.json",
        dict(
            performance_depth=3,
            phases=phases,
            limitations=[
                "One Docker VM; configured zones are not independent physical hosts/disks.",
                "Single PostgreSQL and single S3 gateway are declared dependency SPOFs; no automatic metadata HA qualification.",
                "Paced live workloads and single diagnostic baselines are not sustained or competitive capacity benchmarks.",
                "Warm mount reads do not prove object-store availability; no physical cold-disk benchmark.",
                "CPU/memory/network/I/O are owned-container observations; build workers, host controller and sampling tails are incomplete.",
                "Process rusage and one-shot counters overlap Docker samples; do not add them blindly.",
                "Compression disabled; exact ACK native bytes equal logical body bytes. Replicas, indexes, WAL and temporary files are separate.",
                "Stalled-request cancellation bounds, native restore, physical disk loss, CSI and compaction/retention expiry remain unrun.",
            ],
        ),
    )
    print(lab.ROOT / "reports/QUALIFICATION.json")


if __name__ == "__main__":
    main()
