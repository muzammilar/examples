"""Host-side independent payload oracle and retained-state CephFS checks."""

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

import lab


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def events(text):
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def ssh(node, command, timeout=300):
    return lab.native("guest.py", "ssh", str(node), command, timeout=timeout)


def configuration():
    count = int(os.environ.get("FILES", "128"))
    size = int(os.environ.get("PAYLOAD_BYTES", "262144"))
    if not 1 <= count <= 10000 or not 1 <= size <= 16777216 or count * size > 268435456:
        raise ValueError("Workload exceeds declared bounds")
    return count, size


def fixture():
    value = json.loads((lab.ROOT / "results/fixture.json").read_text())
    fsid = ssh(0, "ceph fsid").stdout.strip()
    if fsid != value["fsid"]:
        raise RuntimeError("Witness belongs to another Ceph cluster; never reseed")
    return value


def cpp(node, mode, run, directory, *, count=None, size=None):
    if not re.fullmatch("[0-9a-f]{32}", run):
        raise ValueError("Unexpected run ID")
    default_count, default_size = configuration()
    count = default_count if count is None else count
    size = default_size if size is None else size
    command = f"env FILES={count} PAYLOAD_BYTES={size} timeout 240 /usr/local/bin/cephfs-files {mode} {run} /var/lib/cephfs-files/{run}"
    result = ssh(node, command, timeout=300)
    (directory / (mode + "-" + str(time.time_ns()) + ".log")).write_text(
        result.stdout + result.stderr
    )
    records = events(result.stdout)
    source = hashlib.sha256(
        (lab.ROOT / "files-workload/main.cpp").read_bytes()
    ).hexdigest()
    if not any(
        r.get("event") == "source" and r.get("sha256") == source for r in records
    ):
        raise RuntimeError("Native executable source hash differs from checkout")
    return records


def export(node, run):
    result = lab.native("workload.py", "export", str(node), run)
    value = json.loads(result.stdout)
    path = (
        lab.ROOT
        / "results"
        / Path(value["export"]).relative_to("/results")
        / "journal.jsonl"
    )
    if hashlib.sha256(path.read_bytes()).hexdigest() != value["sha256"]:
        raise RuntimeError("Export differs from copied witness")
    return path


def history(path, run):
    raw = path.read_bytes()
    if not raw or not raw.endswith(b"\n"):
        raise RuntimeError("Incomplete or absent witness")
    attempts = {}
    outcomes = {}
    for row in map(json.loads, raw.splitlines()):
        sequence = row["sequence"]
        if type(sequence) is not int or sequence < 0:
            raise ValueError("Invalid sequence")
        expected_path = "/objects/" + run + "/" + str(sequence)
        if row["path"] != expected_path or row["run"] != run:
            raise ValueError("Witness path/identity mismatch")
        size = row["bytes"]
        if type(size) is not int or not 1 <= size <= 16777216:
            raise ValueError("Invalid payload size")
        expected = hashlib.sha256(
            hashlib.shake_256(f"42193:{run}:{sequence}".encode()).digest(size)
        ).hexdigest()
        if row["sha256"] != expected:
            raise RuntimeError(
                "Payload differs from independent Python SHAKE/SHA256 oracle"
            )
        if row["event"] == "attempt":
            if sequence in attempts:
                raise ValueError("Duplicate attempt")
            attempts[sequence] = row
        elif row["event"] in ("ack", "ambiguous"):
            if sequence not in attempts or sequence in outcomes:
                raise ValueError("Outcome without unique prior attempt")
            for key in ("path", "bytes", "sha256", "run"):
                if row[key] != attempts[sequence][key]:
                    raise ValueError("Outcome differs from attempt")
            outcomes[sequence] = row["event"]
        else:
            raise ValueError("Unknown publication event")
    return {
        "attempts": len(attempts),
        "acks": sum(v == "ack" for v in outcomes.values()),
        "ambiguous_or_incomplete": len(attempts)
        - sum(v == "ack" for v in outcomes.values()),
        "acknowledged_bytes": sum(
            attempts[k]["bytes"] for k, v in outcomes.items() if v == "ack"
        ),
    }


def verify_export(path, run, directory):
    metadata = history(path, run)
    # Move the verified external witness to a different guest/native client.
    result = lab.native(
        "workload.py", "verify", "2", run, str(path.relative_to(lab.ROOT / "results"))
    )
    (
        directory / ("fresh-verify-" + run + "-" + str(time.time_ns()) + ".log")
    ).write_text(result.stdout + result.stderr)
    rows = events(result.stdout)
    source = hashlib.sha256(
        (lab.ROOT / "files-workload/main.cpp").read_bytes()
    ).hexdigest()
    if not any(r.get("event") == "source" and r.get("sha256") == source for r in rows):
        raise RuntimeError("Fresh verifier executable differs from checkout")
    if not any(
        r.get("event") == "verified"
        and r.get("missing_ack") == 0
        and r.get("wrong") == 0
        and r["acks"] == metadata["acks"]
        for r in rows
    ):
        raise RuntimeError("No complete fresh-client ACK readback")
    return metadata


def audit(directory):
    data = fixture()
    values = []
    for path in sorted((lab.ROOT / "results/cases").glob("*/case.json")):
        case = json.loads(path.read_text())
        if case["fixture"] != data["id"]:
            continue
        if case.get("blocked_before_publication"):
            proof = json.loads((path.parent / "no-publication.json").read_text())
            log = lab.ROOT / proof["writer_log"]
            rows = events(log.read_text())
            if not any(
                r.get("event") == "fatal"
                and r.get("error", "").startswith("mount native CephFS client:")
                for r in rows
            ) or any(
                r.get("event") in ("connected", "attempt", "ack", "ambiguous")
                for r in rows
            ):
                raise RuntimeError(
                    "No-publication claim differs from original failed actor log"
                )
            ssh(
                case["node"],
                "test ! -e /var/lib/cephfs-files/" + case["run"] + "/journal.jsonl",
            )
            continue
        values.append(
            verify_export(export(case["node"], case["run"]), case["run"], directory)
        )
    if not values:
        raise RuntimeError("No acknowledged fixture to audit")
    status = json.loads(ssh(0, "ceph status -f json").stdout)
    if (
        status["osdmap"]["num_up_osds"] != 9
        or status["osdmap"]["num_in_osds"] != 9
        or any(
            v["state_name"] != "active+clean" for v in status["pgmap"]["pgs_by_state"]
        )
    ):
        raise RuntimeError("Native RADOS redundancy is not fully restored")
    for pool in ("labfs_meta", "labfs_data"):
        knobs = json.loads(ssh(0, f"ceph osd pool get {pool} all -f json").stdout)
        if knobs["size"] != 3 or knobs["min_size"] != 2:
            raise RuntimeError("Pool durability differs from contract")
    hosts = {
        int(o["id"]): o["hostname"]
        for o in json.loads(ssh(0, "ceph osd metadata -f json").stdout)
    }
    pgs = json.loads(ssh(0, "ceph pg dump pgs -f json").stdout)["pg_stats"]
    if not pgs or any(
        p["state"] != "active+clean"
        or len(p["acting"]) != 3
        or len({hosts[o] for o in p["acting"]}) != 3
        for p in pgs
    ):
        raise RuntimeError("PG placement does not span three native guest hosts")
    quorum = json.loads(ssh(0, "ceph quorum_status -f json").stdout)
    if set(quorum["quorum_names"]) != {"ceph0", "ceph1", "ceph2"}:
        raise RuntimeError("Monitor quorum not restored")
    save(
        directory / "native-placement.json",
        {"pgs": pgs, "hosts": hosts, "quorum_names": quorum["quorum_names"]},
    )
    result = {
        "histories": values,
        "total_acks": sum(v["acks"] for v in values),
        "acknowledged_bytes": sum(v["acknowledged_bytes"] for v in values),
        "osds_up_in": 9,
        "all_pgs_active_clean": True,
        "pgs_on_three_hosts": len(pgs),
        "monitor_quorum": 3,
    }
    save(directory / "result.json", result)
    return result


def main():
    mode = sys.argv[1]
    if mode not in ("baseline", "verify"):
        raise ValueError(mode)
    directory = lab.ROOT / "results" / (mode + "-" + str(time.time_ns()))
    directory.mkdir(parents=True)
    if mode == "baseline":
        index = lab.ROOT / "results/fixture.json"
        if index.exists():
            raise RuntimeError("Witness exists; verify/recover it instead of reseeding")
        count, size = configuration()
        run = uuid.uuid4().hex
        fsid = ssh(0, "ceph fsid").stdout.strip()
        value = {
            "id": uuid.uuid4().hex,
            "run": run,
            "fsid": fsid,
            "files": count,
            "payload_bytes": size,
            "stage": "initializing",
        }
        save(index, value)
        case = lab.ROOT / "results/cases" / run
        case.mkdir(parents=True)
        save(
            case / "case.json",
            {"fixture": value["id"], "run": run, "node": 1, "mode": "seed"},
        )
        cpp(1, "seed", run, directory, count=count, size=size)
        result = audit(directory)
        if result["total_acks"] != count:
            raise RuntimeError("Seed ACK count differs from declared input")
        records = cpp(2, "read-only-check", run, directory)
        if not any(r.get("write_denied") is True for r in records):
            raise RuntimeError("Read-only role was not proved denied")
        value["stage"] = "ready"
        save(index, value)
    else:
        result = audit(directory)
    print(json.dumps({"mode": mode, "passed": True, "evidence": str(directory)}))


if __name__ == "__main__":
    main()
