"""Offline body/history and fresh native placement/deep-scrub reconciliation."""

import json
import os
from pathlib import Path

from native_state import identities


def read(path):
    return json.loads(path.read_text())


def audit(value):
    hosts = {int(v["id"]): v["hostname"] for v in value["osd_metadata"]}
    if len(hosts) != 9 or set(hosts.values()) != {"ceph0", "ceph1", "ceph2"}:
        raise RuntimeError("native OSD host inventory differs")
    if (
        not value["deep_scrub"]
        or value["pgs"] != len(value["pg_reports"])
        or not value["pg_reports"]
    ):
        raise RuntimeError("missing deep-scrub coverage")
    previous = value["deep_scrub_before"]
    if set(previous) != {p["pgid"] for p in value["pg_reports"]}:
        raise RuntimeError("PG coverage changed")
    for p in value["pg_reports"]:
        if (
            p["state"] != "active+clean"
            or len(p["acting"]) != 3
            or len({hosts[n] for n in p["acting"]}) != 3
            or p["last_deep_scrub_stamp"] == previous[p["pgid"]]
            or p["stat_sum"].get("num_scrub_errors", 0)
        ):
            raise RuntimeError("native placement/checksum recovery incomplete")
    if not value["pools"] or any(
        p["size"] != 3
        or p["min_size"] != 2
        or p["pg_num"] != p["pg_num_target"]
        or p["pgp_num"] != p["pgp_num_target"]
        for p in value["pools"]
    ):
        raise RuntimeError("pool durability/placement changed")
    if set(value["quorum"]["quorum_names"]) != {"ceph0", "ceph1", "ceph2"}:
        raise RuntimeError("MON quorum incomplete")


def freshness(root):
    witnesses = [root / "fault-history.json"]
    witnesses.extend(
        path
        for name in [
            "manifest",
            "cases-manifest",
            "benchmark-manifest",
            "benchmark-attempts",
        ]
        if (path := root / (name + ".json")).exists()
    )
    latest = max(path.stat().st_mtime_ns for path in witnesses)
    for name in ["native-state", "native-audit", "reconciliation"]:
        if (root / (name + ".json")).stat().st_mtime_ns < latest:
            raise RuntimeError("proof predates witness: " + name)
    if (root / "native-state.json").stat().st_mtime_ns < (
        root / "reconciliation.json"
    ).stat().st_mtime_ns:
        raise RuntimeError("native state predates body readback")


def summarize(root):
    required = set()
    for name in ["manifest", "cases-manifest", "benchmark-manifest"]:
        f = root / (name + ".json")
        if f.exists():
            required.update(v["key"] for v in read(f))
    history = read(root / "fault-history.json")
    required.update(v["object"]["key"] for v in history if v["acked"])
    body = read(root / "reconciliation.json")
    if body["objects_verified"] < len(required) or any(
        body[k]
        for k in [
            "missing_acknowledged_keys",
            "corrupt_objects",
            "stale_versions",
            "extra_or_duplicate_keys",
        ]
    ):
        raise RuntimeError("body/ACK reconciliation failed")
    freshness(root)
    fixture = read(root / "fixture.json")
    state = read(root / "native-state.json")
    native = read(root / "native-audit.json")
    if (
        identities(state) != fixture["native_identity"]
        or native["status"]["fsid"] != state["fsid"]
    ):
        raise RuntimeError("native proof belongs to another fixture")
    audit(native)
    cases = []
    for directory in sorted(root.glob("fault-*")):
        if not directory.is_dir():
            continue
        status = read(directory / "status.json")
        if status.get("reason") == "case not completed":
            raise RuntimeError("unfinished case " + directory.name)
        cases.append(
            {
                "case": directory.name,
                **status,
                **(
                    {
                        "result": {
                            key: value
                            for key, value in read(directory / "result.json").items()
                            if key != "native_audit"
                        }
                    }
                    if status["passed"]
                    else {}
                ),
            }
        )
    return {
        "fixture": fixture["id"],
        "attempts": len(history),
        "ack_objects": sum(v["acked"] for v in history),
        "optional_attempts_present": body["objects_verified"] - len(required),
        "reconciliation": body,
        "native_pgs_deep_scrubbed": native["pgs"],
        "copies_per_pg": 3,
        "distinct_hosts_per_pg": 3,
        "cases": cases,
        "scope": "external complete S3 bodies plus native all-PG placement/fresh deep-scrub proof; no independent BlueStore decoder",
    }


if __name__ == "__main__":
    print(
        json.dumps(
            summarize(Path(os.environ.get("RESULTS_DIR", "results/recovery"))), indent=2
        )
    )
