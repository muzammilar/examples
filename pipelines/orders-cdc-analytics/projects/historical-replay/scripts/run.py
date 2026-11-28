"""A related subproject of the owned CDC lab, not a second infrastructure owner."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parents[1]
sys.path.insert(0, str(PARENT / "scripts"))
import lab
import oracle

COMPOSE = [*lab.COMPOSE, "--profile", "historical"]


def compose(*args, timeout=900):
    result = subprocess.run(
        [*COMPOSE, *map(str, args)],
        cwd=PARENT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"Compose exited {result.returncode}: {result.stdout}")
    return result.stdout


def preflight():
    # SQL setup and Spark/replay are mutually exclusive phases, not weaker replicas.
    config = json.loads(compose("--profile", "tools", "config", "--format", "json"))[
        "services"
    ]
    totals = {}
    for name, exclude in [
        ("setup", {"historical", "historical-spark"}),
        ("replay", {"sql"}),
    ]:
        roles = [v for k, v in config.items() if k not in exclude]
        totals[name] = {
            "cpu": sum(
                float(v.get("cpus", 0)) * v.get("deploy", {}).get("replicas", 1)
                for v in roles
            ),
            "memory_bytes": sum(
                int(v.get("mem_limit", 0)) * v.get("deploy", {}).get("replicas", 1)
                for v in roles
            ),
        }
    info = json.loads(
        subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
    )
    for value in totals.values():
        if (
            value["cpu"] > info["NCPU"]
            or value["memory_bytes"] + 2 * 1024**3 > info["MemTotal"]
        ):
            raise RuntimeError(
                "Phased topology exceeds Docker CPU/memory plus 2 GiB reserve"
            )
    return totals


def replay(mode, path, case):
    output = compose(
        "exec",
        "-T",
        "-e",
        "RUN_ID=" + lab.fixture()["id"],
        "-e",
        "CASE_ID=" + case,
        "historical",
        "/replay",
        mode,
    )
    (path / (mode + "-" + str(time.time_ns()) + ".log")).write_text(output)
    return [json.loads(line) for line in output.splitlines()]


def read_events(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines()]
        if path.exists()
        else []
    )


def actor(mode, path, case):
    stream = path.open("x")
    child = subprocess.Popen(
        [
            *lab.COMPOSE,
            "exec",
            "-T",
            "-e",
            "RUN_ID=" + lab.fixture()["id"],
            "-e",
            "MUTATION_STEPS=2000",
            "-e",
            "CASE_ID=" + case,
            "orders",
            "/orders",
            mode,
        ],
        cwd=PARENT,
        stdout=stream,
        stderr=subprocess.STDOUT,
    )
    return child, stream


def main():
    if sys.argv[1] == "check":
        print(json.dumps(preflight()))
        return
    if sys.argv[1] != "test":
        raise ValueError("unknown mode")
    path = ROOT / "results" / ("replay-" + str(time.time_ns()))
    path.mkdir(parents=True)
    lab.save(
        path / "status.json", {"passed": False, "scope": "level-3 historical replay"}
    )
    lab.save(path / "budgets.json", preflight())
    # A native SQL-client actor cannot overlap the memory/CPU slot used by Spark.
    if lab.command("ps", "--quiet", "--status", "running", "sql").strip():
        raise RuntimeError("SQL setup must have exited before historical phase")
    lab.workload("verify", path)
    case = uuid.uuid4().hex
    shared = PARENT / "results/historical" / case
    shared.mkdir(parents=True)
    lab.save(
        path / "identity.json",
        {
            "fixture": lab.fixture(),
            "case": case,
            "data": str(shared.relative_to(PARENT)),
        },
    )
    compose("up", "-d", "historical")
    replay("export", path, case)
    # Immutable snapshot gets its original source version, never publication time.
    output = compose(
        "run", "--rm", "-e", "CASE_ID=" + case, "historical-spark", timeout=600
    )
    (path / "spark.log").write_text(output)
    parts = list((shared / "spark-output").glob("part-*.csv"))
    if len(parts) != 1:
        raise RuntimeError("Expected exactly one bounded ordered Spark output")
    (shared / "joined.csv").hardlink_to(parts[0])
    checked = oracle.verify(shared)
    checked["input_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [shared / "orders.csv", shared / "customers.csv"]
    }
    raw = (shared / "joined.csv").read_bytes()
    checked["reference_zlib_bytes"] = len(zlib.compress(raw))
    checked["reference_compression_scope"] = (
        "CSV reference only, not Doris native compression"
    )
    lab.save(path / "snapshot-oracle.json", checked)
    writer, ws = actor("mutate", path / "writer.log", case)
    reader, rs = actor("reader", path / "reader.log", case)
    publisher = None
    ps = None
    try:
        journal = (
            PARENT
            / "results"
            / ("mutate-" + case + "-" + lab.fixture()["id"] + ".jsonl")
        )
        ack_count = lambda: sum(e.get("event") == "ack" for e in read_events(journal))
        lab.wait_for(lambda: ack_count() >= 5)
        ps = (path / "publisher.log").open("x")
        publisher = subprocess.Popen(
            [
                *COMPOSE,
                "exec",
                "-T",
                "-e",
                "RUN_ID=" + lab.fixture()["id"],
                "-e",
                "CASE_ID=" + case,
                "historical",
                "/replay",
                "prepare",
            ],
            cwd=PARENT,
            stdout=ps,
            stderr=subprocess.STDOUT,
        )
        lab.wait_for(lambda: (shared / "prepared.json").exists(), timeout=120)
        replay("status", path, case)
        lab.wait_for(lambda: ack_count() >= 110)
        identifier = compose("ps", "--quiet", "historical").strip()
        original = lab.assert_owned(identifier, "historical")
        before = ack_count()
        subprocess.run(
            ["docker", "kill", "--signal", "SIGKILL", identifier],
            check=True,
            capture_output=True,
        )
        killed = lab.assert_owned(identifier, "historical")
        lab.save(
            path / "fault.json",
            {
                "before": original["State"],
                "after": killed["State"],
                "acks_before": before,
                "id": identifier,
            },
        )
        if (
            killed["State"]["Running"]
            or killed["State"]["ExitCode"] != 137
            or killed["State"]["OOMKilled"]
        ):
            raise RuntimeError("Owned publisher crash not proved")
        publisher.wait(timeout=30)
        ps.close()
        lab.wait_for(lambda: ack_count() > before)
        lab.save(path / "live-progress.json", {"before": before, "after": ack_count()})
        compose("start", "historical")
        replay("status", path, case)
        replay("commit", path, case)
        replay(
            "commit", path, case
        )  # Same labels/transactions; never create a new logical batch.
        (
            PARENT / "results" / ("stop-writer-" + lab.fixture()["id"] + "-" + case)
        ).touch()
        if writer.wait(timeout=120) != 0:
            raise RuntimeError("Source writer failed; preserve witness")
        replay("race", path, case)
        lab.workload("verify", path)
    finally:
        for mode, child, stream in [("writer", writer, ws), ("reader", reader, rs)]:
            stop = (
                PARENT
                / "results"
                / ("stop-" + mode + "-" + lab.fixture()["id"] + "-" + case)
            )
            stop.touch()
            try:
                if child.wait(timeout=45) != 0:
                    raise RuntimeError(mode + " failed; inspect retained log")
            finally:
                stream.close()
        if publisher is not None and publisher.poll() is None:
            raise RuntimeError("Publisher still active; preserved for diagnosis")
    for replica in range(3):
        output = lab.command(
            "exec",
            "-T",
            "-e",
            "RUN_ID=" + lab.fixture()["id"],
            "-e",
            "REPLICA_INDEX=" + str(replica),
            "orders",
            "/orders",
            "verify",
            timeout=1000,
        )
        (path / ("replica-" + str(replica) + ".log")).write_text(output)
    lab.native(path)
    resource_log = subprocess.check_output(
        [sys.executable, str(PARENT / "scripts/resources.py")], cwd=PARENT, text=True
    )
    (path / "resource-inventory-path.txt").write_text(resource_log)
    lab.save(
        path / "status.json",
        {
            "passed": True,
            "case": case,
            "fixture": lab.fixture()["id"],
            "snapshot_rows": checked["rows"],
            "expected": "newer live source rows and cancellations survive stale replay; all three fixed replicas exact",
        },
    )
    print(json.dumps({"passed": True, "evidence": str(path)}))


if __name__ == "__main__":
    main()
