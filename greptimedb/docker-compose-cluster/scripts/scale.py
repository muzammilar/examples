"""Native region migration during continuous writes, 3 -> 5 -> 3 datanodes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from live_observer import LiveObserver

root = Path("results")
mode = sys.argv[1]
if mode not in ("out", "in"):
    raise ValueError(mode)
case = "scale-" + mode
project = os.environ["COMPOSE_PROJECT_NAME"]


def compose(*args, capture=False):
    return subprocess.run(
        ["docker", "compose", *args], check=True, text=True, capture_output=capture
    ).stdout


def query(sql):
    raw = compose(
        "exec",
        "-T",
        "front0",
        "curl",
        "--fail-with-body",
        "-sS",
        "--data-urlencode",
        "sql=" + sql,
        "http://localhost:4000/v1/sql",
        capture=True,
    )
    result = json.loads(raw)
    if result.get("error") or result.get("code", 0):
        raise RuntimeError(result)
    return result["output"][0]["records"]["rows"]


def migrate(region, source, target):
    query(f"ADMIN migrate_region({region},{source},{target},120)")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        rows = query(
            f"SELECT peer_id,status FROM information_schema.region_peers WHERE region_id={region}"
        )
        if len(rows) == 1 and int(rows[0][0]) == target and rows[0][1] == "ALIVE":
            return
        time.sleep(1)
    raise RuntimeError("migration timeout")


observer = LiveObserver(
    case,
    [
        "docker",
        "compose",
        "run",
        "--rm",
        "-e",
        "FAULT_CASE=" + case,
        "observer",
        "watch",
    ],
)

log = (root / (case + "-load.log")).open("w")
name = project + "-load-" + case
load = subprocess.Popen(
    [
        "docker",
        "compose",
        "run",
        "--rm",
        "--name",
        name,
        "-e",
        "FAULT_CASE=" + case,
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
        raise RuntimeError("producer failed before scaling")
    observer.mark("operation")
    begin = time.monotonic()
    if mode == "out":
        compose("--profile", "scale", "up", "-d", "--wait", "data3", "data4")
        rows = query(
            "SELECT region_id,peer_id FROM information_schema.region_peers WHERE table_name='fleet_load' ORDER BY region_id"
        )
        if len(rows) != 3:
            raise RuntimeError("expected three logical fleet regions")
        for row, target in zip(rows[:2], (3, 4)):
            migrate(int(row[0]), int(row[1]), target)
    else:
        rows = query(
            "SELECT region_id,peer_id FROM information_schema.region_peers WHERE peer_id>=3"
        )
        for region, source in rows:
            migrate(int(region), int(source), int(region) % 3)
        if query(
            "SELECT region_id FROM information_schema.region_peers WHERE peer_id>=3"
        ):
            raise RuntimeError("nodes not drained")
        compose("--profile", "scale", "stop", "data3", "data4")
    elapsed = time.monotonic() - begin
    time.sleep(5)
    if load.poll() is not None:
        raise RuntimeError("producer expired during scaling")
    observer.mark("after")
    observer.progress()
    (root / ("stop-" + case)).touch()
    if load.wait(timeout=150) != 0:
        raise RuntimeError("producer failed")
    observations = observer.finish()
    compose("run", "--rm", "-e", "FAULT_CASE=" + case, "client", "verify-fault")
    compose("run", "--rm", "client", "verify")
    result = {
        "observations": observations,
        "transition": "3 -> 5" if mode == "out" else "5 -> 3",
        "migration_seconds": elapsed,
        "regions": query(
            "SELECT table_name,region_id,peer_id,status FROM information_schema.region_peers"
        ),
        "reconciliation": json.loads(
            (root / ("fault-" + case + "-reconciliation.json")).read_text()
        ),
    }
    (root / (case + "-result.json")).write_text(json.dumps(result, indent=2) + "\n")
finally:
    observer.close()
    if load.poll() is None:
        subprocess.run(["docker", "stop", "--timeout", "5", name], check=False)
        load.wait(timeout=30)
    log.close()
