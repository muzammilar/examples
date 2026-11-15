"""Independent destructive tests, external ACK oracle, and native replica recovery.

Only resources identified by this Compose project are touched. Disk mode destroys
one owned data volume after stopping its process; this models a lost/replaced disk,
not Linux EIO or simultaneous host power failure.
"""

import importlib.util
import collections
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def run(*args, capture=False, **kw):
    result = subprocess.run(
        list(args), check=True, text=True, capture_output=capture, **kw
    )
    return result.stdout if capture else None


def compose(*args, **kw):
    return run("docker", "compose", *args, **kw)


def tool(*args):
    compose("run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", *args)


mode = sys.argv[1]
if mode not in ["process", "disk"]:
    raise ValueError("unknown failure mode")
spec = importlib.util.spec_from_file_location("adapter", Path("scripts/adapter.py"))
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)
base = Path(os.environ.get("RESULTS_DIR", "results/verified"))
case_name = (
    "fault-" + mode + ("-" + adapter.TARGET if os.environ.get("FAULT_TARGET") else "")
)
case = base / case_name
if case.exists():
    raise RuntimeError("fault case already exists; use a clean lifecycle")
case.mkdir(parents=True)
for name in ["manifest", "cases-manifest", "benchmark-manifest", "fault-history"]:
    p = base / (name + ".json")
    if p.exists():
        shutil.copy2(p, case / p.name)
os.environ["RESULTS_DIR"] = str(case)
os.environ["DURATION"] = os.environ.get("FAULT_DURATION", "600")
adapter.audit("snapshot")
adapter.audit("wait")
ids = compose("ps", "-q", capture=True).split()
before = json.loads(run("docker", "inspect", *ids, capture=True))
containers = {c["Config"]["Labels"]["com.docker.compose.service"]: c for c in before}
target = containers[adapter.TARGET]
volume = (
    next(m["Name"] for m in target["Mounts"] if m["Destination"] == adapter.DISK)
    if mode == "disk"
    else None
)
project = target["Config"]["Labels"]["com.docker.compose.project"]
for name in [volume] if volume else []:
    info = json.loads(run("docker", "volume", "inspect", name, capture=True))[0]
    if info["Labels"]["com.docker.compose.project"] != project:
        raise RuntimeError("refusing foreign volume")
log = (case / "load.log").open("w")
load_name = project + "-" + case_name + "-load"
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
        raise RuntimeError("load exited before failure")
    # Docker Linux monotonic clock, shared with the workload containers.
    compose("kill", "-s", "SIGKILL", adapter.TARGET)
    fault_start = float(
        compose(
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "tools",
            "-c",
            "import time; print(time.monotonic())",
            capture=True,
        ).strip()
    )
    (case / "fault-clock.json").write_text(
        json.dumps({"fault_start": fault_start}) + "\n"
    )
    if mode == "disk":
        compose("rm", "-f", adapter.TARGET)
        if hasattr(adapter, "release_disk_references"):
            adapter.release_disk_references()
        run("docker", "volume", "rm", volume)
        adapter.prepare_empty(project, target["Mounts"])
    time.sleep(30)
    fault_end = float(
        compose(
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "tools",
            "-c",
            "import time; print(time.monotonic())",
            capture=True,
        ).strip()
    )
    (case / "fault-clock.json").write_text(
        json.dumps({"fault_start": fault_start, "fault_end": fault_end}) + "\n"
    )
    restart_start = time.monotonic()
    compose("up", "-d", "--wait", adapter.TARGET)
    ready_seconds = time.monotonic() - restart_start
    adapter.repair()
    # Keep the producer running while all pre-fault physical replicas recover.
    adapter.audit("wait")
    shutil.copy2(case / "physical-recovery.json", case / "baseline-recovery.json")
    if load.poll() is not None:
        raise RuntimeError(
            "producer duration expired before native recovery; increase FAULT_DURATION"
        )
    (case / "stop-churn").touch()
    if load.wait(timeout=60) != 0:
        raise RuntimeError("churn failed")
finally:
    if load.poll() is None:
        subprocess.run(
            ["docker", "stop", "--time", "5", load_name],
            check=False,
            stdout=subprocess.DEVNULL,
        )
        load.wait(timeout=20)
    log.close()
    journal = case / "churn.jsonl"
    records = (
        [json.loads(s) for s in journal.read_text().splitlines()]
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
counts = collections.Counter(r["stage"] for r in records)
# Full byte verification includes every baseline/benchmark object and all ACKs;
# unacknowledged PUTs may be present only with their originally attempted bytes.
tool("tools", "verify")
adapter.repair()
adapter.audit("snapshot")
adapter.audit("wait")
after_ids = compose("ps", "-q", capture=True).split()
after = json.loads(run("docker", "inspect", *after_ids, capture=True))
after_by_service = {
    c["Config"]["Labels"]["com.docker.compose.service"]: c for c in after
}
for service, c in containers.items():
    if service != adapter.TARGET and after_by_service[service]["Id"] != c["Id"]:
        raise RuntimeError("survivor unexpectedly replaced: " + service)
first_ack = next(
    (
        r["time"] - fault_start
        for r in records
        if r["stage"] == "ack" and r["time"] >= fault_start
    ),
    None,
)
report = {
    "failure": mode,
    "target": adapter.TARGET,
    "unavailable_seconds": fault_end - fault_start,
    "acks_during_outage": sum(
        r["stage"] == "ack" and fault_start <= r["time"] <= fault_end for r in records
    ),
    "gets_during_outage": sum(
        r["stage"] == "get" and fault_start <= r["time"] <= fault_end for r in records
    ),
    "counts": dict(counts),
    "first_ack_after_fault_seconds": first_ack,
    "process_ready_seconds": ready_seconds,
    "survivor_containers_unchanged": len(containers) - 1,
    "reconciliation": json.loads((case / "reconciliation.json").read_text()),
    "physical_recovery": json.loads((case / "physical-recovery.json").read_text()),
    "preexisting_replica_recovery": json.loads(
        (case / "baseline-recovery.json").read_text()
    ),
}
(case / "result.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
