"""Compute crash, local-metadata disk loss and S3 backend member loss, separately."""

import collections
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def compose(*args, capture=False):
    r = subprocess.run(
        ["docker", "compose", *args], check=True, text=True, capture_output=capture
    )
    return r.stdout


def runner(*args, capture=False):
    return compose(
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--entrypoint",
        "python",
        "runner",
        "/app/fault_workload.py",
        *args,
        capture=capture,
    )


mode = sys.argv[1]
if mode not in ["process", "disk", "backend"]:
    raise ValueError(mode)
phase = {"process": 0, "disk": 1, "backend": 2}[mode]
base = Path(os.environ["RESULTS_DIR"])
case = base / ("fault-" + mode)
case.mkdir()
for name in ["seed", "expected", "fault-history"]:
    p = base / (name + ".json")
    if p.exists():
        shutil.copy2(p, case / p.name)
os.environ["RESULTS_DIR"] = str(case)
runner("verify")
services = json.loads(
    subprocess.check_output(
        ["docker", "inspect", *compose("ps", "-q", capture=True).split()], text=True
    )
)
by_service = {s["Config"]["Labels"]["com.docker.compose.service"]: s for s in services}
target = "garage0" if mode == "backend" else "ch0"
volume = None
if mode == "disk":
    volume = next(
        m["Name"]
        for m in by_service[target]["Mounts"]
        if m["Destination"] == "/var/lib/clickhouse"
    )
    info = json.loads(
        subprocess.check_output(["docker", "volume", "inspect", volume], text=True)
    )[0]
    if (
        info["Labels"]["com.docker.compose.project"]
        != os.environ["COMPOSE_PROJECT_NAME"]
    ):
        raise RuntimeError("refusing foreign metadata volume")
name = os.environ["COMPOSE_PROJECT_NAME"] + "-load-" + mode
with (case / "load.log").open("w") as log:
    load = subprocess.Popen(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--name",
            name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--entrypoint",
            "python",
            "runner",
            "/app/fault_workload.py",
            "churn",
            str(phase),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(10)
        if load.poll() is not None:
            raise RuntimeError("load exited before injection")
        compose("kill", "-s", "SIGKILL", target)
        began = float(runner("clock", capture=True).strip())
        if mode == "disk":
            compose("rm", "-f", target)
            subprocess.run(["docker", "volume", "rm", volume], check=True)
        time.sleep(30)
        ended = float(runner("clock", capture=True).strip())
        if mode == "disk":
            runner("drop-replica")
        recovery = time.monotonic()
        compose("up", "-d", "--wait", target)
        runner("restore" if mode == "disk" else "rejoin")
        recovery_seconds = time.monotonic() - recovery
        if load.poll() is not None:
            raise RuntimeError("producer stopped before recovery")
        (case / "stop-churn").touch()
        if load.wait(timeout=180) != 0:
            raise RuntimeError("load failed")
    finally:
        if load.poll() is None:
            subprocess.run(["docker", "stop", "--time", "5", name], check=False)
            load.wait(timeout=20)
        journal = case / "churn.jsonl"
        records = (
            [json.loads(line) for line in journal.read_text().splitlines()]
            if journal.exists()
            else []
        )
        history_file = base / "fault-history.json"
        history = json.loads(history_file.read_text()) if history_file.exists() else []
        history.extend(records)
        with (base / "fault-history.tmp").open("w") as f:
            json.dump(history, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(base / "fault-history.tmp", history_file)
runner("verify")
after = json.loads(
    subprocess.check_output(
        ["docker", "inspect", *compose("ps", "-q", capture=True).split()], text=True
    )
)
after = {s["Config"]["Labels"]["com.docker.compose.service"]: s for s in after}
for service, before in by_service.items():
    if service != target and before["Id"] != after[service]["Id"]:
        raise RuntimeError("survivor replaced: " + service)
counts = collections.Counter(r["stage"] for r in records)
report = {
    "failure": mode,
    "target": target,
    "outage_seconds": ended - began,
    "recovery_seconds_after_restart_command": recovery_seconds,
    "counts": dict(counts),
    "acks_during_outage": sum(
        r["stage"] == "ack" and began <= r["time"] <= ended for r in records
    ),
    "acknowledged_rows_during_outage": 10000
    * sum(r["stage"] == "ack" and began <= r["time"] <= ended for r in records),
    "reconciliation": json.loads((case / "reconciliation.json").read_text()),
    "survivor_containers_unchanged": len(by_service) - 1,
}
(case / "result.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
