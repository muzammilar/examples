"""Curate this bounded probe; keep syscall output and native dumps ignored."""

import json
from pathlib import Path
import subprocess
import sys

import diagnose
from report import native_cgroup


def main():
    case, observer, before, after = [Path(value).resolve() for value in sys.argv[1:]]
    if not json.loads((case / "status.json").read_text())["passed"]:
        raise RuntimeError("Do not qualify a failed probe")
    result = json.loads((case / "result.json").read_text())
    result["date"] = "2026-11-27"
    result["performance_depth"] = 3
    result["classification"] = (
        "One bounded syscall-stage diagnostic, not a competitive benchmark"
    )
    result["compression_reference"] = result.get("compression_reference") or json.loads(
        (case / "compression-reference.json").read_text()
    )
    result["docker_resources"] = json.loads(
        subprocess.check_output(
            [
                sys.executable,
                str(diagnose.ROOT / "scripts/resource_report.py"),
                str(observer),
            ],
            text=True,
        )
    )
    first, last = [json.loads(path.read_text()) for path in (before, after)]
    result["storage_before"] = first["volumes"]
    result["storage_after"] = last["volumes"]
    result["native_cgroups"] = {}
    for role in last["native_cgroups"]:
        old, new = [
            native_cgroup(snapshot["native_cgroups"][role])
            for snapshot in (first, last)
        ]
        old_io, new_io = old["lifetime_block_io"], new["lifetime_block_io"]
        result["native_cgroups"][role] = {
            "memory_peak_bytes": new["memory_peak_bytes"],
            "cpu_core_s_delta": (
                new["lifetime_cpu_core_s"] - old["lifetime_cpu_core_s"]
                if old["lifetime_cpu_core_s"] is not None
                and new["lifetime_cpu_core_s"] is not None
                else None
            ),
            "io_delta": (
                {key: value - old_io.get(key, 0) for key, value in new_io.items()}
                if old_io is not None and new_io is not None
                else None
            ),
            "collector_error": new["collector_error"],
        }
    rows = [
        json.loads(line) for line in (case / "publication.log").read_text().splitlines()
    ]
    result["application_resources"] = next(
        row for row in rows if row.get("event") == "resources"
    )
    result["source_sha256"] = next(
        row["sha256"] for row in rows if row.get("event") == "source"
    )
    result["evidence"] = {
        name: str(path.relative_to(diagnose.ROOT))
        for name, path in zip(
            ("case", "observer", "before", "after"), (case, observer, before, after)
        )
    }
    result["upstream_source"] = {
        "rename": "https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/mount/weedfs_rename.go",
        "lock_client": "https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/cluster/lock_client.go",
        "interpretation": "Rename acquires DLM locks and calls the filer. The lock client sleeps one second on errors, contention or redirects; this is a candidate, not a traced cause in this run.",
    }
    result["limits"] += [
        "Native cgroup deltas include exact verification, resource probes and the later compression reference. Docker's stage-probe window excludes that later reference. Do not add the scopes together.",
        "Distroless etcd native I/O operation counts unavailable; Docker sampling and volume sizing still include those roles.",
        "Native storage codec disabled/unmeasured; gzip is an independent identical-body compressibility reference.",
        "No lock-path RPC trace or isolated cause, no claim that disabling DLM is an equivalent workaround.",
    ]
    path = Path(__file__).parent / "reports/DIAGNOSIS-2026-11-27.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(path)


if __name__ == "__main__":
    main()
