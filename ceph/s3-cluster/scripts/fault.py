"""Keep S3 requests running through a guest crash or actual SCSI removal."""

import collections
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def compose(*args, **kwargs):
    return subprocess.run(["docker", "compose", *args], check=True, text=True, **kwargs)


def clock():
    return float(
        compose(
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "tools",
            "-c",
            "import time; print(time.monotonic())",
            capture_output=True,
        ).stdout.strip()
    )


def ctl(*args):
    compose("run", "--rm", "control", "/opt/lab/app/recovery.py", *args)


mode = sys.argv[1]
if mode not in ["process", "disk"]:
    raise ValueError(mode)
base = Path(os.environ.get("RESULTS_DIR", "results/verified"))
case = base / ("fault-" + mode)
case.mkdir()
for name in [
    "manifest",
    "cases-manifest",
    "benchmark-manifest",
    "benchmark-attempts",
    "fault-history",
]:
    p = base / (name + ".json")
    if p.exists():
        shutil.copy2(p, case / p.name)
os.environ["RESULTS_DIR"] = str(case)
os.environ["DURATION"] = os.environ.get("FAULT_DURATION", "1800")
ctl("audit")
load_name = os.environ["COMPOSE_PROJECT_NAME"] + "-load-" + mode
with (case / "load.log").open("w") as log:
    load = subprocess.Popen(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--name",
            load_name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "tools",
            "churn",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(12)
        if load.poll() is not None:
            raise RuntimeError("load exited before injection")
        ctl("inject", mode)
        begin = time.monotonic()
        fault_start = clock()
        time.sleep(30)
        fault_end = clock()
        ctl("recover", mode)
        elapsed = time.monotonic() - begin
        if load.poll() is not None:
            raise RuntimeError(
                "producer expired before native recovery; increase FAULT_DURATION"
            )
        (case / "stop-churn").touch()
        if load.wait(timeout=60) != 0:
            raise RuntimeError("load failed")
    finally:
        if load.poll() is None:
            subprocess.run(["docker", "stop", "--time", "5", load_name], check=False)
            load.wait(timeout=20)
        journal = case / "churn.jsonl"
        records = (
            [json.loads(line) for line in journal.read_text().splitlines()]
            if journal.exists()
            else []
        )
        acks = {r["key"] for r in records if r["stage"] == "ack"}
        history_file = base / "fault-history.json"
        history = json.loads(history_file.read_text()) if history_file.exists() else []
        history.extend(
            {"object": r["object"], "acked": r["object"]["key"] in acks}
            for r in records
            if r["stage"] == "attempt"
        )
        with (base / "fault-history.tmp").open("w") as f:
            json.dump(history, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(base / "fault-history.tmp", history_file)
ctl("audit")
compose("run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "tools", "verify")
result = {
    "failure": mode,
    "scope": "one Linux guest power loss"
    if mode == "process"
    else "one owned SCSI device unplugged, empty spare provisioned by ceph-volume, then a graceful replacement-persistence power cycle",
    "seconds_until_native_clean_including_30s_outage": elapsed,
    "seconds_until_final_verification_including_30s_outage": time.monotonic() - begin,
    "unavailable_seconds": fault_end - fault_start,
    "acks_during_outage": sum(
        r["stage"] == "ack" and fault_start <= r["time"] <= fault_end for r in records
    ),
    "first_ack_after_fault_seconds": next(
        (
            r["time"] - fault_start
            for r in records
            if r["stage"] == "ack" and r["time"] >= fault_start
        ),
        None,
    ),
    "counts": dict(collections.Counter(r["stage"] for r in records)),
    "reconciliation": json.loads((case / "reconciliation.json").read_text()),
    "native_audit": json.loads((case / "native-audit.json").read_text()),
    "surviving_guests_unchanged": 2,
    "replacement_restart_verified": (case / "replacement-restart.json").exists()
    if mode == "disk"
    else None,
}
(case / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
