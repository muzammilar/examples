"""Compact good/bad outcomes; leave raw histories, native logs and samples ignored."""

import hashlib
import datetime
import json
import math
from pathlib import Path
import statistics
import lab
from resource_report import summarize


def timing(values):
    if not values:
        return None
    samples = sorted(values)
    return dict(
        count=len(samples),
        mean_ms=statistics.mean(samples),
        p50_ms=statistics.median(samples),
        p99_ms=samples[max(0, math.ceil(0.99 * len(samples)) - 1)],
        max_ms=max(samples),
        p999_ms=None,
        note="Nearest-rank p99; fewer than 100 samples makes p99 effectively maximum; no established p99.9",
    )


def compact_verification(row):
    return dict(
        snapshot=row["snapshot"]["snapshot"],
        metadata=row["snapshot"]["metadata"],
        table_uuid=row["snapshot"]["table_uuid"],
        snapshot_count=len(row["snapshot"]["snapshots"]),
        oracle=row["oracle"],
        spark=row["spark"],
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
        if (folder / "source-sha256.json").exists():
            row["source_manifest_sha256"] = hashlib.sha256(
                (folder / "source-sha256.json").read_bytes()
            ).hexdigest()
        if (folder / "before-cgroups.json").exists() and (
            folder / "after-cgroups.json"
        ).exists():
            row["resources"] = summarize(folder)
            combined = row["resources"]["native_retained_role_deltas"].copy()
            for actor in row["resources"]["actor_lifetime_counters"]:
                for key in ("cpu_us", "rbytes", "wbytes", "rios", "wios"):
                    if key in actor["counters"]:
                        combined[key] = combined.get(key, 0) + actor["counters"][key]
            row["resources"]["observed_native_plus_actor_lower_bound"] = combined
        else:
            row["resources"] = dict(
                unavailable="No full collector window; see failure/status"
            )
        producers, reads, compactions = [], [], []
        for file in folder.glob("*.log"):
            for record in lab.records(file):
                if "last_batch_ms" in record:
                    producers.append(record["last_batch_ms"])
                if record.get("read") == "ok":
                    reads.append(record["wall_ms"])
                if "compaction" in record:
                    compactions.append(record["compaction"])
        row["producer_1000_record_batch_with_witness_timing"] = timing(producers)
        row["pinned_old_parquet_body_read_timing"] = timing(reads)
        if compactions:
            row["compaction_receipts"] = compactions
        for name in (
            "baseline",
            "verification",
            "final-verification",
            "retained-verify",
            "post-drain-verification",
        ):
            file = folder / (name + ".json")
            if file.exists():
                row[name] = compact_verification(json.loads(file.read_text()))
        file = folder / "recovery.json"
        if file.exists():
            recovery = json.loads(file.read_text())
            for key in ("final", "frozen_read"):
                recovery[key] = compact_verification(recovery[key])
            row["recovery"] = recovery
        file = folder / "retention.json"
        if file.exists():
            row["retention"] = json.loads(file.read_text())
        for name in ("candidate-export", "remnants"):
            file = folder / (name + ".json")
            if file.exists():
                row[name] = json.loads(file.read_text())
        if (folder / "checkpoints.json").exists():
            native = json.loads((folder / "checkpoints.json").read_text())
            row["native_checkpoints"] = dict(counts=native["counts"], restored=native["latest"]["restored"])
        if (folder / "fault-killed.json").exists() and (folder / "fault-restored.json").exists():
            killed = json.loads((folder / "fault-killed.json").read_text())
            restarted = json.loads((folder / "fault-restored.json").read_text())
            end = datetime.datetime.fromisoformat(killed["State"]["FinishedAt"].replace('Z','+00:00'))
            start = datetime.datetime.fromisoformat(restarted["State"]["StartedAt"].replace('Z','+00:00'))
            row["fault"] = dict(role=killed["Config"]["Labels"]["com.docker.compose.service"],
                                exit_code=killed["State"]["ExitCode"], oom=killed["State"]["OOMKilled"],
                                killed_at=killed["State"]["FinishedAt"], restarted_at=restarted["State"]["StartedAt"],
                                process_down_s=(start-end).total_seconds())
        if (folder / "native-failed-tasks.json").exists():
            tasks = json.loads((folder / "native-failed-tasks.json").read_text())
            row["failed_spark_tasks"] = [dict(stage=t['Stage ID'], reason=t['Task End Reason']) for t in tasks]
        volumes = sorted(folder.glob("volumes-*.json"))
        if volumes:
            row["storage_samples"] = [
                {
                    k: v
                    for k, v in json.loads(f.read_text()).items()
                    if not k.startswith("_")
                }
                for f in volumes
            ]
        phases.append(row)
    report = dict(
        performance_depth=3,
        phases=phases,
        limitations=[
            "One shared Docker VM, simulated zones and no physical power-loss proof.",
            "Paced one-million-message phases are correctness/recovery diagnostics, not sustained capacity.",
            "Producer timing is a 1000-record ProduceSync batch plus local witness work; not individual record latency.",
            "Pinned reader measures one previously verified Parquet body, not full-table SQL or latest-snapshot visibility.",
            "Snapshot timestamp is allocated before commit; it does not prove publication visibility time.",
            "Per-phase resources include verification and background maintenance; one-shot tools are separately reported.",
            "Native cgroup I/O excludes cache hits; disk service time/queue depth and isolated codec CPU unavailable.",
            "Volume allocation is not unique physical extent usage; container temporary-layer peaks and host instrumentation overhead are incomplete.",
            "No all-role HA, independent restore, update/delete semantics or destructive orphan expiration.",
        ],
    )
    destination = lab.ROOT / "reports/QUALIFICATION.json"
    lab.save(destination, report)
    print(destination)


if __name__ == "__main__":
    main()
