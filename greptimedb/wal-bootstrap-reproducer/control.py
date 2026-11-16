"""Deliberately fail metadata WAL initialization, then check acknowledged replay."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path("results")
root.mkdir(exist_ok=True)
mode = sys.argv[1]
compose = ["docker", "compose"]
control = os.environ.get("CONTROL", "0") == "1"


def run(*args, capture=False, check=True, timeout=180):
    return subprocess.run(
        list(args), check=check, text=True, capture_output=capture, timeout=timeout
    )


def logs(service):
    return run(*compose, "logs", "--no-color", service, capture=True).stdout


def create_topic():
    existing = json.loads(
        run(
            *compose,
            "exec",
            "-T",
            "wal",
            "rpk",
            "topic",
            "list",
            "--format=json",
            capture=True,
        ).stdout
    )
    if any(topic["name"] == "bootstrap_repro_0" for topic in existing):
        return
    run(
        *compose,
        "exec",
        "-T",
        "wal",
        "rpk",
        "topic",
        "create",
        "bootstrap_repro_0",
        "--replicas",
        "1",
        "--partitions",
        "1",
    )


def first_record():
    return run(
        *compose,
        "exec",
        "-T",
        "wal",
        "rpk",
        "topic",
        "consume",
        "bootstrap_repro_0",
        "--offset",
        "0",
        "--num",
        "1",
        "--format",
        "%o key=%k value-bytes=%V\n",
        capture=True,
        timeout=20,
    ).stdout.strip()


if mode == "infrastructure":
    if (root / "seed.json").exists():
        raise RuntimeError(
            "preserve completed/failed evidence and archive it before another fresh fixture"
        )
    run(*compose, "build", "wal-workload")
    run(*compose, "up", "-d", "--wait", "etcd", "wal")
    run(
        *compose,
        "exec",
        "-T",
        "wal",
        "rpk",
        "cluster",
        "config",
        "set",
        "write_caching_default",
        "false",
    )
    run(
        *compose,
        "exec",
        "-T",
        "wal",
        "rpk",
        "cluster",
        "config",
        "set",
        "auto_create_topics_enabled",
        "false",
    )
    if control:
        create_topic()
    run(*compose, "up", "-d", "--wait", "meta")
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        output = logs("meta")
        if (
            "Activated topic pool with 1 topics"
            if control
            else "Failed to notify listener: WalProvider"
        ) in output:
            (root / "metadata-initialization.log").write_text(output)
            break
        time.sleep(1)
    else:
        raise RuntimeError("expected metadata initialization outcome not observed")
    (root / "mode.json").write_text(
        json.dumps(
            {"healthy_control": control, "at": datetime.now(timezone.utc).isoformat()}
        )
    )
elif mode == "seed":
    if not control:
        create_topic()
    run(*compose, "up", "-d", "--wait", "data", "front")
    run(*compose, "run", "--rm", "-T", "wal-workload", "seed")
    run(*compose, "run", "--rm", "-T", "wal-workload", "verify")
    (root / "readback.json").rename(root / "before-restart.json")
elif mode == "restart":
    if (root / "restart-result.json").exists():
        raise RuntimeError("restart result exists; preserve it before another attempt")
    cid = run(*compose, "ps", "-q", "data", capture=True).stdout.strip()
    info = json.loads(run("docker", "inspect", cid, capture=True).stdout)[0]
    if (
        info["Config"]["Labels"].get("com.docker.compose.project")
        != "greptime-wal-bootstrap"
    ):
        raise RuntimeError("refusing foreign datanode")
    record = first_record()
    started = datetime.now(timezone.utc).isoformat()
    run(*compose, "kill", "--signal", "SIGKILL", "data")
    killed = json.loads(run("docker", "inspect", cid, capture=True).stdout)[0]
    if killed["State"]["Running"] or killed["State"]["ExitCode"] != 137:
        raise RuntimeError("datanode SIGKILL not proven")
    run(*compose, "up", "-d", "--wait", "data")
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        output = run(
            *compose, "logs", "--no-color", "--since", started, "data", capture=True
        ).stdout
        if "Replay WAL for region:" in output:
            (root / "restart-replay.log").write_text(output)
            break
        time.sleep(1)
    else:
        raise RuntimeError("native replay completion not observed")
    result = run(*compose, "run", "--rm", "-T", "wal-workload", "verify", check=False)
    readback = json.loads((root / "readback.json").read_text())
    report = {
        "healthy_control": control,
        "offset_zero": record,
        "native_exit_code": killed["State"]["ExitCode"],
        "readback": readback,
        "bug_reproduced": readback["missing_rows"] > 0,
    }
    (root / "restart-result.json").write_text(json.dumps(report, indent=2) + "\n")
    if report["bug_reproduced"]:
        raise RuntimeError(
            "acknowledged data lost; see restart-result.json; failure deliberately remains nonzero"
        )
    if result.returncode:
        raise RuntimeError("readback failed without the expected missing-row signature")
elif mode == "guard":
    record = first_record()
    (root / "bootstrap-guard.json").write_text(
        json.dumps(
            {"offset_zero": record, "safe": record == "0 key= value-bytes=0"}, indent=2
        )
    )
    if record != "0 key= value-bytes=0":
        raise RuntimeError("unsafe offset-zero WAL initialization; refuse new writes")
else:
    raise ValueError(mode)
