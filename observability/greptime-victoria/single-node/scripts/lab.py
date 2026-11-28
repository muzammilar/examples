"""Native commands and ownership for this example only."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "obs-greptime-victoria-single"
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
os.environ.setdefault("LAB_UID", str(os.getuid()))
BACKENDS = {"greptime": ["greptime"], "victoria": ["vmetrics", "vlogs"]}


def command(*args, check=True, timeout=600):
    result = subprocess.run(
        [*COMPOSE, *args], cwd=ROOT, text=True, capture_output=True, timeout=timeout
    )
    if check and result.returncode:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".next")
    with pending.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)


def phase(value):
    pending = ROOT / "results/phase.next"
    pending.write_text(value)
    pending.replace(ROOT / "results/phase")


def owned(role):
    identifier = command("ps", "--all", "--quiet", role).stdout.strip()
    if not identifier or "\n" in identifier:
        raise RuntimeError("Expected one owned " + role)
    value = json.loads(
        subprocess.check_output(["docker", "inspect", identifier], text=True)
    )[0]
    labels = value["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != role
    ):
        raise RuntimeError("Foreign container")
    return value


def workload(*args, check=True):
    return command(
        "run",
        "--rm",
        "--no-deps",
        "telemetry-workload",
        *args,
        check=check,
        timeout=900,
    )


def assert_source(path):
    source = (ROOT / "telemetry-workload").resolve(strict=True)
    expected = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for pattern in ["*.go", "*.yaml"]
        for p in source.glob(pattern)
    }
    for name in ["go.mod", "go.sum"]:
        expected[name] = hashlib.sha256((source / name).read_bytes()).hexdigest()
    result = workload("--mode=manifest")
    (path / "manifest-tool.log").write_text(result.stderr)
    text = result.stdout
    observed = {line.split()[1]: line.split()[0] for line in text.splitlines()}
    if observed != expected:
        raise RuntimeError("Rebuild workload: native manifest differs from checkout")
    save(path / "source-manifest.json", expected)


def select_backend(name, path):
    phase(name + ":backend-selection")
    if name not in BACKENDS:
        raise ValueError("Invalid backend")
    for rival, roles in BACKENDS.items():
        if rival != name:
            command("stop", *roles)
    command("up", "--detach", *BACKENDS[name])
    until = time.monotonic() + 120
    while time.monotonic() < until:
        check = workload("--mode=health", "--backend=" + name, check=False)
        with (path / (name + "-health-tools.log")).open("a") as stream:
            stream.write(check.stdout + "\n" + check.stderr + "\n")
        if check.returncode == 0:
            break
        time.sleep(2)
    else:
        raise RuntimeError("Backend did not become healthy: " + check.stderr)
    snapshots = {}
    for role in BACKENDS[name]:
        value = owned(role)
        snapshots[role] = {
            "id": value["Id"],
            "image": value["Config"]["Image"],
            "memory_limit": value["HostConfig"]["Memory"],
            "nano_cpus": value["HostConfig"]["NanoCpus"],
            "mounts": value["Mounts"],
            "started_at": value["State"]["StartedAt"],
        }
    if (
        sum(v["memory_limit"] for v in snapshots.values()) != 4 * 2**30
        or sum(v["nano_cpus"] for v in snapshots.values()) != 4_000_000_000
    ):
        raise RuntimeError("Effective backend budget is not 4 CPU / 4 GiB")
    for rival, roles in BACKENDS.items():
        if rival != name:
            for role in roles:
                identifier = command("ps", "--quiet", role).stdout.strip()
                if identifier:
                    raise RuntimeError("Rival still running: " + role)
    save(path / (name + "-environment.json"), snapshots)


def disk_snapshot(backend, path, label):
    measured = {}
    for role in BACKENDS[backend]:
        owner = owned(role)
        for mount in owner["Mounts"]:
            if mount["Type"] != "volume" or mount["Name"] in measured:
                continue
            labels = json.loads(
                subprocess.check_output(
                    ["docker", "volume", "inspect", mount["Name"]], text=True
                )
            )[0].get("Labels", {})
            if labels.get("com.docker.compose.project") != PROJECT:
                raise RuntimeError("Foreign data volume")
            result = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--pull=never",
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--cpus",
                    ".1",
                    "--memory",
                    "64m",
                    "--user",
                    owner["Config"]["User"] or "0",
                    "--label",
                    "com.docker.compose.project=" + PROJECT,
                    "--label",
                    "com.docker.compose.service=disk-observer",
                    "--mount",
                    "type=volume,source=" + mount["Name"] + ",target=/data,readonly",
                    "obs-greptime-victoria-single-workload:local",
                    "--mode=disk",
                ],
                text=True,
                capture_output=True,
                timeout=120,
            )
            (path / (role + "-disk-helper-" + label + ".log")).write_text(
                result.stdout + "\n" + result.stderr
            )
            if result.returncode:
                raise RuntimeError("Owned disk snapshot unavailable: " + result.stderr)
            snapshot = json.loads(result.stdout)
            if (
                label == "before-ingest"
                and snapshot["filesystem"]["filesystem_available_bytes"] < 8 * 2**30
            ):
                raise RuntimeError("Fewer than 8 GiB free before ingestion")
            measured[mount["Name"]] = {"role": role, **snapshot}
    save(
        path / (backend + "-disk-" + label + ".json"),
        {
            "volumes": measured,
            "allocated_data_bytes": sum(
                v["allocated_bytes"] for v in measured.values()
            ),
            "logical_file_bytes": sum(
                v["logical_regular_file_bytes"] for v in measured.values()
            ),
        },
    )
