"""Replay the ZFS follow-up's exact read/body and native-repair evidence."""

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))
import evidence
import lab

ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text())


def native_read_phases(records, actions):
    operations = {
        kind: [action for action in actions if action["command"][1] == kind]
        for kind in ("offline", "replace", "scrub")
    }
    if any(not selected for selected in operations.values()) or any(
        action["exit_code"] for action in actions
    ):
        raise RuntimeError("native phase evidence incomplete or failed")

    def start(kind):
        return min(a["started_monotonic_ns"] for a in operations[kind])

    def end(kind):
        return max(a["ended_monotonic_ns"] for a in operations[kind])

    edges = [
        ("baseline", 0, start("offline")),
        ("fault-injection", start("offline"), end("offline")),
        ("degraded", end("offline"), start("replace")),
        ("replacement-workflow", start("replace"), end("replace")),
        ("post-replacement-verification", end("replace"), start("scrub")),
        ("scrub", start("scrub"), end("scrub")),
        ("after-scrub", end("scrub"), 2**63),
    ]
    if any(right < left for _, left, right in edges):
        raise RuntimeError("native operations are not serial and ordered")
    classified = []
    for record in records:
        if record.get("kind") != "read":
            continue
        matches = [
            name
            for name, left, right in edges
            if left
            <= record["started_monotonic_ns"]
            <= record["ended_monotonic_ns"]
            <= right
        ]
        name = matches[0] if matches else "spans-transition"
        classified.append(dict(record, phase_start=name, phase_end=name))
    summary = evidence.summarize_reads(classified)
    for name, left, right in edges:
        if name not in summary:
            summary[name] = {
                "ok": 0,
                "fail": 0,
                "mismatch": 0,
                "mean_ms": None,
                "p50_ms": None,
                "p99_ms": None,
                "scope": "No complete read wholly inside this native interval; transition-spanning reads are separate, not zero latency.",
            }
        summary[name]["native_started_monotonic_ns"] = left if left else None
        summary[name]["native_ended_monotonic_ns"] = right if right != 2**63 else None
    for phase in (
        "baseline",
        "degraded",
        "replacement-workflow",
        "scrub",
        "after-scrub",
    ):
        if not summary.get(phase, {}).get("ok"):
            raise RuntimeError(
                "actual native phase lacks successful independent reads: " + phase
            )
    return summary


def fio_metrics(value, direction):
    job = value["jobs"][0]
    io = job[direction]
    return {
        "bytes": io["io_bytes"],
        "operations": io["total_ios"],
        "iops": io["iops"],
        "runtime_ms": io["runtime"],
        "completion_latency_ns": io.get("clat_ns"),
        "fsync": job.get("sync"),
        "job_user_cpu_percent": job.get("usr_cpu"),
        "job_system_cpu_percent": job.get("sys_cpu"),
        "scope": "Native fio job over entire fault/verification workload. Buffered write completion is not individual durable commit; fsync statistics separate. No per-phase write latency history or per-flush external ACK journal. Diagnostic, not capacity.",
    }


def case_report(path):
    if (path / "failure.json").exists() or (path / "cleanup-failure.json").exists():
        raise RuntimeError("failed case cannot qualify: " + path.name)
    value = load(path / "result.json")
    expected = load(path / "immutable-ack.json")
    records = [
        json.loads(line) for line in (path / "reader.jsonl").read_text().splitlines()
    ]
    reads = [r for r in records if r.get("kind") == "read"]
    if not reads or records[-1].get("kind") != "reader-stopped":
        raise RuntimeError("reader history incomplete")
    if any(r["result"] != "ok" or r["actual"] != expected for r in reads):
        raise RuntimeError(
            "immutable body differs from durable external acknowledgement"
        )
    summary = evidence.summarize_reads(records)
    if summary != value["read_summary"]:
        raise RuntimeError("read summary differs from raw history")
    for phase in ("baseline", "degraded", "replacement", "repaired", "after-scrub"):
        if summary.get(phase, {}).get("ok", 0) == 0:
            raise RuntimeError("missing continuous observation phase: " + phase)
    for action in value["replacement_native_intervals"]:
        if action["exit_code"] or not evidence.reads_within(
            records, action["started_monotonic_ns"], action["ended_monotonic_ns"]
        ):
            raise RuntimeError("replacement interval lacks successful complete read")
    for key, verification in (("write_fio", False), ("verification_fio", True)):
        lab.checked_fio(value[key], value["data_mib"], verification)
    if (
        value["after"]["health"] != "ONLINE"
        or "No known data errors" not in value["after"]["status"]
    ):
        raise RuntimeError("native redundancy/scrub health not restored")
    actions = [load(p) for p in sorted(path.glob("action-*.json"))]
    actual_phases = native_read_phases(records, actions)
    return {
        "id": path.name,
        "config": load(path / "config.json"),
        "immutable_ack": expected,
        "controller_tagged_read_summary": summary,
        "native_read_phase_summary": actual_phases,
        "read_phase_scope": "Canonical phases derived from same-guest native-command monotonic intervals. Reads spanning transitions remain separately counted; controller tags can lag native changes. Resource phase brackets additionally include SSH/controller overhead.",
        "successful_immutable_reads": len(reads),
        "native_replacement_intervals": value["replacement_native_intervals"],
        "replacement_seconds_including_controller": value["replacement_seconds"],
        "scrub_seconds_including_controller": value["scrub_seconds"],
        "payload_file": value["file"],
        "verified_payload_bytes": value["data_mib"] * 1024**2,
        "fio_write": fio_metrics(value["write_fio"], "write"),
        "fio_verification_read": fio_metrics(value["verification_fio"], "read"),
        "bytes_progressed_while_degraded": value["bytes_progressed_while_degraded"],
        "faulted_members": value["faulted_members"],
        "before": value["before"],
        "degraded": value["degraded"],
        "after": value["after"],
        "compression": value["compression"],
        "raw_result_sha256": hashlib.sha256(
            (path / "result.json").read_bytes()
        ).hexdigest(),
        "read_history_sha256": hashlib.sha256(
            (path / "reader.jsonl").read_bytes()
        ).hexdigest(),
    }


def check_final_observations(cleanup, fencing):
    expected = {"containers", "volumes", "networks", "image_tag"}
    if set(cleanup["healthy_runtime_lists"]) != expected or any(
        cleanup["healthy_runtime_lists"].values()
    ):
        raise RuntimeError(
            "healthy native listings do not prove complete owned cleanup"
        )
    for role, pools in (("source", []), ("target", ["zfslab"])):
        observation = fencing[role]
        if (
            observation["exit_code"]
            or observation["stderr"]
            or observation["pools"] != pools
        ):
            raise RuntimeError(
                "healthy native source fencing/target ownership is not proven"
            )


def publish(folder):
    folder = folder.resolve(strict=True)
    if not folder.is_relative_to((ROOT / "results").resolve()):
        raise RuntimeError("select example-owned results")
    paths = sorted((folder / "cases").glob("*"))
    if any(not (path / "result.json").exists() for path in paths):
        raise RuntimeError("unfinished case remains in cohort")
    cases = [case_report(path) for path in paths]
    if {c["config"]["layout"] for c in cases} != {"mirror", "raidz2"}:
        raise RuntimeError("require both mirror and RAIDZ2 observations")
    runs = []
    for path in sorted((folder / "runs").glob("*")):
        completion = path / "completion.json"
        if not completion.exists():
            raise RuntimeError("incomplete lifecycle operation: " + path.name)
        source_hashes = load(path / "sources.json")
        for name, digest in source_hashes.items():
            source = (ROOT / name).resolve(strict=True)
            if (
                not source.is_relative_to(ROOT)
                or hashlib.sha256(source.read_bytes()).hexdigest() != digest
            ):
                raise RuntimeError("tested runtime source changed: " + name)
        result = load(completion)
        if (
            result["exit_code"]
            or result["sampling_errors"]
            or not result["sampler_stopped"]
        ):
            raise RuntimeError("lifecycle/resource recorder failed")
        environment = load(path / "environment.json")
        docker = environment["docker"]
        runs.append(
            {
                "environment": {
                    "host_system": environment["host_system"],
                    "host_architecture": environment["host_architecture"],
                    "host_kernel": environment["host_kernel"],
                    "members_at_start": environment["members"],
                    "docker": {
                        key: docker[key]
                        for key in (
                            "ServerVersion",
                            "KernelVersion",
                            "Architecture",
                            "NCPU",
                            "MemTotal",
                            "CgroupVersion",
                        )
                    },
                    "scope": environment["scope"],
                },
                "id": path.name,
                "completion": result,
                "sources": load(path / "sources.json"),
                "resources": load(path / "resource-summary.json"),
                "phases": load(path / "case-phase-resources.json"),
                "host_controller": load(path / "host-controller-resources.json")
                if (path / "host-controller-resources.json").exists()
                else None,
                "teardown": load(path / "teardown-verification.json")
                if (path / "teardown-verification.json").exists()
                else None,
            }
        )
    if not any(
        run["teardown"] is not None and not any(run["teardown"].values())
        for run in runs
    ):
        raise RuntimeError("healthy runtime teardown verification missing")
    replication = load(folder / "replication.json")
    if (
        replication["clone"]
        != {"payload": replication["baseline"], "delta": replication["delta"]}
        or replication["baseline"]["bytes"] != cases[0]["config"]["data_mib"] * 1024**2
        or replication["delta"]["bytes"] != 64 * 1024**2
        or not replication["source_pool_exported"]
        or replication["replicated_bytes_missing"]
        or replication["unreplicated_tail_absent_bytes"] != 64 * 1024**2
        or replication["new_promoted_bytes_verified"] != 64 * 1024**2
        or not replication["interrupted"]["interrupted"]
        or replication["interrupted"]["bytes"] != 65536
        or replication["resumed"]["interrupted"]
        or replication["resumed"]["bytes"] <= 0
    ):
        raise RuntimeError(
            "snapshot replication/resume/promotion evidence differs from its contract"
        )
    archive_proofs = []
    for path in sorted((folder / "pool-archives").glob("*/verified.json")):
        proof = load(path)
        body = path.parent / "pool.zfs"
        with body.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != proof["sha256"] or body.stat().st_size != proof["bytes"]:
            raise RuntimeError("preserved native archive changed")
        archive_proofs.append(
            {
                key: proof[key]
                for key in (
                    "node",
                    "snapshot",
                    "dataset_snapshots",
                    "bytes",
                    "sha256",
                    "native_stream_check_exit_code",
                    "scope",
                )
            }
        )
    if not archive_proofs:
        raise RuntimeError("no preserved native pool evidence")
    cleanup = load(folder / "post-cleanup.json")
    fencing = load(folder / "post-promotion-fencing.json")
    check_final_observations(cleanup, fencing)
    report = {
        "passed": True,
        "published_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "hardware": load(folder / "hardware.json"),
        "native_versions": load(folder / "versions.json"),
        "effective_arc_observation": load(folder / "arc-effective.json"),
        "effective_limits_observation": load(folder / "effective-limits.json"),
        "network_lifetime_observation": load(folder / "network-lifetime.json"),
        "post_promotion_fencing": fencing,
        "post_cleanup": cleanup,
        "controller_exclusion": load(folder / "controller-exclusion-verified.json"),
        "auxiliary_inconclusive_attempt": load(
            folder / "auxiliary-attempts/status-probe.json"
        ),
        "earlier_failed_attempt": load(folder / "prior-attempt.json")
        if (folder / "prior-attempt.json").exists()
        else None,
        "cases": cases,
        "replication": replication,
        "replication_raw_sha256": hashlib.sha256(
            (folder / "replication.json").read_bytes()
        ).hexdigest(),
        "runs": runs,
        "native_archives": archive_proofs,
        "scope": "Functional mirror/RAIDZ2 immutable-reader and active-fio recovery observations. Software emulation/shared host and concurrent independent functional lanes; no comparative throughput winner, independent-host durability, per-flush external ACK, native-stream restore or continuous replication qualification. Native send archives are re-hashed here; prior zstream checksum results are preserved, not replaced by a restore claim.",
    }
    (ROOT / "OBSERVER_RESULTS.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    publish(parser.parse_args().results)
