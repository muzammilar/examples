"""Independent compute/WAL crashes and empty-volume replacement under writes."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from live_observer import LiveObserver

mode = sys.argv[1]
if mode not in ("process", "disk", "compute", "cache"):
    raise ValueError(mode)
project = os.environ["COMPOSE_PROJECT_NAME"]
root = Path("results")
case = mode + "-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime())
target = "compute" if mode in ("compute", "cache") else "sk0"
if mode == "process":
    target = os.environ.get("FAULT_SERVICE", "sk0")
    if target not in ("sk0", "sk1", "sk2"):
        raise ValueError("FAULT_SERVICE must be sk0, sk1 or sk2")
compose = ["docker", "compose", "--profile", "workload"]


def run(*args, capture=False):
    return subprocess.run(
        list(args), check=True, text=True, capture_output=capture, timeout=300
    ).stdout


def tool(action, capture=False):
    return run(*compose, "run", "--rm", "-T", "runner", action, capture=capture)


def journal():
    path = root / ("fault-" + case + ".jsonl")
    return (
        [json.loads(line) for line in path.read_text().split("\n")[:-1]]
        if path.exists()
        else []
    )


def progress(after=0):
    end = time.monotonic() + 180
    while time.monotonic() < end:
        if load.poll() is not None:
            raise RuntimeError("producer exited before recovery")
        if sum(r["stage"] == "ack" and r["at"] > after for r in journal()) >= 3:
            return
        time.sleep(1)
    raise RuntimeError("no three new acknowledged batches before deadline")


def lsn(value):
    hi, lo = value.split("/")
    return (int(hi, 16) << 32) + int(lo, 16)


container = run(*compose, "ps", "-q", target, capture=True).strip()
info = json.loads(run("docker", "inspect", container, capture=True))[0]
if info["Config"]["Labels"].get("com.docker.compose.project") != project:
    raise RuntimeError("refusing foreign target")
volume = None
if mode in ("disk", "cache"):
    volume = next(m["Name"] for m in info["Mounts"] if m["Type"] == "volume")
    value = json.loads(run("docker", "volume", "inspect", volume, capture=True))[0]
    if (
        value["Labels"].get("com.docker.compose.project") != project
        or value["Labels"].get("com.docker.compose.volume") != target
    ):
        raise RuntimeError("refusing foreign disk")
observer = LiveObserver(
    case, [*compose, "run", "--rm", "-e", "FAULT_CASE=" + case, "observer", "watch"]
)

log = (root / (case + "-load.log")).open("w")
load_name = project + "-fault-" + case
load = subprocess.Popen(
    [
        *compose,
        "run",
        "--rm",
        "--name",
        load_name,
        "-e",
        "FAULT_CASE=" + case,
        "runner",
        "churn",
    ],
    stdout=log,
    stderr=subprocess.STDOUT,
)
try:
    progress()
    observer.progress()
    observer.mark("outage")
    began = time.monotonic()
    injected = time.time()
    run(*compose, "kill", "--signal", "SIGKILL", target)
    if mode in ("disk", "cache"):
        run(*compose, "rm", "--force", target)
        run("docker", "volume", "rm", volume)
    time.sleep(30)
    if target.startswith("sk"):
        progress(after=injected)
    observer.mark("recovery")
    run(*compose, "up", "--detach", "--wait", "--wait-timeout", "180", target)
    if mode == "disk":
        tool("recover-wal")
    progress(after=time.time())
    # The recovered third WAL copy must reach the last externally witnessed LSN.
    end = time.monotonic() + 180
    while time.monotonic() < end:
        acks = [r for r in journal() if r["stage"] == "ack"]
        expected = max(lsn(r["flush_lsn"]) for r in acks)
        statuses = json.loads(tool("wal-status", capture=True))
        if len(statuses) == 3 and all(
            lsn(s["flush_lsn"]) >= expected and lsn(s["commit_lsn"]) >= expected
            for s in statuses.values()
        ):
            break
        time.sleep(2)
    else:
        raise RuntimeError("all three safekeepers did not catch up to acknowledged WAL")
    recovered = time.monotonic() - began
    progress(after=time.time())
    observer.mark("after")
    observer.progress()
    (root / ("stop-" + case)).touch()
    if load.wait(timeout=60) != 0:
        raise RuntimeError("producer failed")
    observations = observer.finish()
    tool("verify-recovery")
    tool("verify")
    rows = journal()
    result = {
        "case": case,
        "observations": observations,
        "target": target,
        "scope": "empty owned volume replacement"
        if volume
        else "SIGKILL with retained volume",
        "removed_volume": volume,
        "recovery_seconds_including_30s_hold": recovered,
        "final_verified_seconds": time.monotonic() - began,
        "acknowledged_rows": sum(r["count"] for r in rows if r["stage"] == "ack"),
        "ambiguous_batches": sum(r["stage"] == "ambiguous" for r in rows),
        "wal_status": statuses,
        "reconciliation": json.loads((root / "recovery-history.json").read_text()),
    }
    (root / (case + "-result.json")).write_text(json.dumps(result, indent=2) + "\n")
finally:
    observer.close()
    if load.poll() is None:
        (root / ("stop-" + case)).touch()
        try:
            load.wait(timeout=30)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "stop", "--timeout", "5", load_name], check=False)
            load.wait(timeout=30)
    with (root / (case + "-diagnostics.log")).open("w") as diagnostics:
        subprocess.run(
            [*compose, "logs", "--tail", "80", target],
            stdout=diagnostics,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
    log.close()
