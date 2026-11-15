"""Fault only this Compose project's owned datanode; producer remains live."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from live_observer import LiveObserver


def compose(*args, capture=False):
    return subprocess.run(
        ["docker", "compose", *args], check=True, text=True, capture_output=capture
    ).stdout


def query(sql):
    raw = compose(
        "exec",
        "-T",
        "front1",
        "curl",
        "--max-time",
        "60",
        "-fsS",
        "--data-urlencode",
        "sql=" + sql,
        "http://localhost:4000/v1/sql",
        capture=True,
    )
    value = json.loads(raw)
    if value.get("error") or value.get("code", 0):
        raise RuntimeError(value)
    return value["output"][0]["records"]["rows"]


mode = sys.argv[1]
if mode not in ("process", "disk"):
    raise ValueError(mode)
project = os.environ["COMPOSE_PROJECT_NAME"]
root = Path("results")
out = root / ("fault-" + mode + "-result.json")
if out.exists():
    raise RuntimeError("use a clean lifecycle before repeating this fault case")
load_name = project + "-load-" + mode
placement = query(
    "SELECT region_id,peer_id,status FROM information_schema.region_peers WHERE table_name='fleet_load'"
)
owners = {int(r[1]) for r in placement if r[2] == "ALIVE"}
if not owners or not owners <= {0, 1, 2}:
    raise RuntimeError("unexpected workload owner")
target = min(owners)
service = f"data{target}"
before = {
    n: compose("ps", "-q", n, capture=True).strip()
    for n in [*(f"data{i}" for i in range(3) if i != target), "front0", "front1"]
}
observer = LiveObserver(
    mode,
    [
        "docker",
        "compose",
        "run",
        "--rm",
        "-e",
        "FAULT_CASE=" + mode,
        "observer",
        "watch",
    ],
)

log = (root / ("fault-" + mode + "-load.log")).open("w")
load = subprocess.Popen(
    [
        "docker",
        "compose",
        "run",
        "--rm",
        "--name",
        load_name,
        "-e",
        "FAULT_CASE=" + mode,
        "client",
        "churn",
    ],
    stdout=log,
    stderr=subprocess.STDOUT,
)
try:
    observer.progress()
    time.sleep(8)
    if load.poll() is not None:
        raise RuntimeError("producer exited before injection")
    observer.mark("operation")
    start = time.monotonic()
    fault_begin = datetime.now(timezone.utc)
    compose("kill", "-s", "SIGKILL", service)
    time.sleep(30)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        moved = query(
            "SELECT region_id,peer_id,status FROM information_schema.region_peers WHERE table_name='fleet_load'"
        )
        if len(moved) == 3 and all(
            int(r[1]) != target and r[2] == "ALIVE" for r in moved
        ):
            break
        if load.poll() is not None:
            raise RuntimeError("producer expired during failover")
        time.sleep(2)
    else:
        raise RuntimeError("regions did not fail over to surviving datanodes")
    failover_seconds = time.monotonic() - start
    fault_end = datetime.now(timezone.utc)
    if mode == "disk":
        compose("rm", "-f", service)
        volume = project + "_gt-" + service
        info = json.loads(
            subprocess.check_output(["docker", "volume", "inspect", volume], text=True)
        )[0]
        if info["Labels"].get("com.docker.compose.project") != project or info[
            "Labels"
        ].get("com.docker.compose.volume") != ("gt-" + service):
            raise RuntimeError("volume ownership mismatch")
        subprocess.run(["docker", "volume", "rm", volume], check=True)
    compose("up", "-d", "--wait", service)
    # Native route health, not just a running process. Lost-node regions can move
    # to another live datanode because SST and WAL remain independently durable.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        placement = query(
            "SELECT region_id,peer_id,status FROM information_schema.region_peers WHERE table_name='fleet_load'"
        )
        if len(placement) == 3 and all(r[2] == "ALIVE" for r in placement):
            break
        time.sleep(2)
    else:
        raise RuntimeError("workload region routes did not recover")
    native = time.monotonic() - start
    time.sleep(5)
    if load.poll() is not None:
        raise RuntimeError("producer exited before recovery finished")
    observer.mark("after")
    observer.progress()
    (root / ("stop-" + mode)).touch()
    if load.wait(timeout=150) != 0:
        raise RuntimeError("producer failed")
    observations = observer.finish()
    compose("run", "--rm", "-e", "FAULT_CASE=" + mode, "client", "verify-fault")
    compose("run", "--rm", "client", "verify")
    after = {n: compose("ps", "-q", n, capture=True).strip() for n in before}
    if before != after:
        raise RuntimeError("a surviving service was recreated")
    records = [
        json.loads(line)
        for line in (root / ("fault-" + mode + ".jsonl")).read_text().splitlines()
    ]
    acknowledgements = [
        datetime.fromisoformat(r["at"].replace("Z", "+00:00"))
        for r in records
        if r["stage"] == "ack"
    ]
    acks_during = sum(fault_begin <= t <= fault_end for t in acknowledgements) * 100
    first_ack = next(
        (
            (t - fault_begin).total_seconds()
            for t in acknowledgements
            if t >= fault_begin
        ),
        None,
    )
    reconciliation = json.loads(
        (root / ("fault-" + mode + "-reconciliation.json")).read_text()
    )
    out.write_text(
        json.dumps(
            {
                "case": mode,
                "observations": observations,
                "failed_datanode": target,
                "minimum_hold_seconds": 30,
                "observed_hold_seconds": (fault_end - fault_begin).total_seconds(),
                "native_failover_seconds": failover_seconds,
                "acknowledged_rows_during_hold": acks_during,
                "first_ack_after_injection_seconds": first_ack,
                "native_routes_ready_seconds": native,
                "final_verified_seconds": time.monotonic() - start,
                "placement_after": placement,
                "survivors_unchanged": len(before),
                "reconciliation": reconciliation,
            },
            indent=2,
        )
        + "\n"
    )
finally:
    observer.close()
    subprocess.run(["docker", "compose", "up", "-d", "--wait", service], check=True)
    if load.poll() is None:
        subprocess.run(["docker", "stop", "--time", "5", load_name], check=False)
        load.wait(timeout=30)
    log.close()
