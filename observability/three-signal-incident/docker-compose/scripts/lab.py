"""Commands, immutable evidence and resource ownership for this one local lab."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "obs-three-signal-incident"
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
os.environ.setdefault("LAB_UID", str(os.getuid()))
BACKENDS = {
    "greptime": ["greptime", "greptime-collector"],
    "victoria": ["vmetrics", "vlogs", "vtraces", "victoria-collector"],
    "grafana": ["prometheus", "loki", "tempo", "grafana-collector"],
}
ACTORS = ["incident-workload", "incident-publisher", "incident-reader"]


def command(*arguments, check=True, timeout=600):
    result = subprocess.run(
        [*COMPOSE, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and result.returncode:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result


def tool(*arguments, check=True):
    return command(
        "run", "--rm", "--no-deps", "incident-workload", *arguments, check=check
    )


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".next")
    with pending.open("w") as file:
        json.dump(value, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
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


def running_actors():
    result = []
    for role in ACTORS:
        identifiers = subprocess.check_output(
            [
                "docker",
                "ps",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=" + PROJECT,
                "--filter",
                "label=com.docker.compose.service=" + role,
            ],
            text=True,
        )
        result.extend(identifiers.splitlines())
    return result


def assert_source(path):
    path.mkdir(parents=True, exist_ok=True)
    result = tool("--mode=manifest")
    (path / "source-manifest.log").write_text(result.stderr)
    manifest = {}
    for line in result.stdout.splitlines():
        digest, name = line.split(maxsplit=1)
        source = ROOT / "incident-workload" / name
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Workload image/source differ: " + name)
        manifest[name] = digest
    save(path / "source-manifest.json", manifest)


def select_backend(backend, path):
    if running_actors():
        raise RuntimeError("Cannot switch backend while any actor is live")
    phase("select:" + backend)
    for rival, roles in BACKENDS.items():
        if rival != backend:
            command("stop", *roles)
            for role in roles:
                if command("ps", "--all", "--quiet", role).stdout.strip():
                    stopped = owned(role)
                    save(
                        path / "stops" / (role + "-" + str(time.time_ns()) + ".json"),
                        {
                            "id": stopped["Id"],
                            "state": stopped["State"],
                            "reference": stopped["Config"]["Image"],
                            "mounts": stopped["Mounts"],
                            "scope": "Observed after stop and before any subsequent start; exit 137 differs from clean exit 0",
                        },
                    )
    if backend == "grafana":
        for role in ["prometheus", "loki", "tempo"]:
            initialized = command("run", "--rm", "--no-deps", role + "-storage-init")
            with (path / (role + "-storage-init.log")).open("a") as file:
                file.write(initialized.stdout + "\n" + initialized.stderr)
    command("up", "--detach", *BACKENDS[backend])
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = tool("--mode=health", "--backend=" + backend, check=False)
        with (path / (backend + "-health.log")).open("a") as file:
            file.write(result.stdout + "\n" + result.stderr)
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("Backend/collector did not become ready: " + result.stderr)
    metadata = {}
    for role in BACKENDS[backend]:
        value = owned(role)
        metadata[role] = {
            "id": value["Id"],
            "image_id": value["Image"],
            "reference": value["Config"]["Image"],
            "cpu": value["HostConfig"]["NanoCpus"],
            "memory_bytes": value["HostConfig"]["Memory"],
            "mounts": value["Mounts"],
        }
    # Compose serializes fractional CPUs through a float32 path (5.5 CPUs can
    # appear as 5,500,000,256 NanoCPUs). Permit only sub-microcore rounding.
    if (
        abs(sum(v["cpu"] for v in metadata.values()) - 6000000000) > 1024
        or sum(v["memory_bytes"] for v in metadata.values()) != 8 * 2**30
    ):
        raise RuntimeError("Effective total backend budget is not 6 CPU / 8 GiB")
    for rival, roles in BACKENDS.items():
        if rival != backend and any(
            command("ps", "--quiet", role).stdout.strip() for role in roles
        ):
            raise RuntimeError("Rival backend still running")
    save(path / (backend + "-environment.json"), metadata)


def fence(directory):
    if running_actors():
        raise RuntimeError("Cannot freeze histories while an actor is live")
    hashes = {}
    for signal in ["metrics", "logs", "traces"]:
        source = directory / signal / "requests.jsonl"
        hashes[signal] = hashlib.sha256(source.read_bytes()).hexdigest()
    save(
        directory / "fenced.json",
        {"actors_stopped": True, "histories": hashes, "fenced_at_unix_s": time.time()},
    )


def verify(backend, fixture, directory, report, projection=False):
    result = tool(
        "--mode=" + ("verify-projection" if projection else "verify"),
        "--backend=" + backend,
        "--fixture=/" + str(fixture.relative_to(ROOT)),
        "--output=/" + str(directory.relative_to(ROOT)),
        "--report=/" + str(report.relative_to(ROOT)),
        check=False,
    )
    report.with_suffix(".log").write_text(result.stdout + "\n" + result.stderr)
    if result.returncode:
        raise RuntimeError("Native body oracle failed: " + result.stderr)
    value = json.loads(report.read_text())
    missing = False
    for signal, record in value["signals"].items():
        if not record["complete"] or record["counts"]["unexpected_ids"]:
            raise RuntimeError("Incomplete or unexpected " + signal + " readback")
        missing = missing or bool(record["counts"]["missing_acknowledged_ids"])
    return value, missing


def unused_prometheus_volume(role, owner, mount, labels, image):
    """Recognize only the pinned image's unused mount for read-only accounting."""
    return (
        role == "prometheus"
        and mount["Destination"] == "/prometheus"
        and mount["Destination"] in (image["Config"].get("Volumes") or {})
        and "--storage.tsdb.path=/data" in owner["Config"]["Cmd"]
        and labels in ({}, {"com.docker.volume.anonymous": ""})
    )


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
            ownership = None
            if labels.get("com.docker.compose.project") != PROJECT:
                image = json.loads(
                    subprocess.check_output(
                        ["docker", "image", "inspect", owner["Image"]], text=True
                    )
                )[0]
                if not unused_prometheus_volume(role, owner, mount, labels, image):
                    raise RuntimeError("Foreign data volume")
                ownership = {
                    "scope": "Read-only inspection of unused image-declared anonymous volume",
                    "owned_container_id": owner["Id"],
                    "image_id": owner["Image"],
                    "declared_destination": mount["Destination"],
                    "active_tsdb_path": "/data",
                }
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
                    {
                        "prometheus": "65534:65534",
                        "loki": "10001:10001",
                        "tempo": "10001:10001",
                    }.get(role, owner["Config"]["User"] or "0"),
                    "--label",
                    "com.docker.compose.project=" + PROJECT,
                    "--label",
                    "com.docker.compose.service=disk-observer",
                    "--mount",
                    "type=volume,source=" + mount["Name"] + ",target=/data,readonly",
                    "obs-three-signal-incident-workload:local",
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
            measured[mount["Name"]] = {
                "role": role,
                "ownership_note": ownership,
                **snapshot,
            }
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


def metrics_snapshot(backend, path, label):
    for role in BACKENDS[backend]:
        result = tool("--mode=scrape", "--metric-role=" + role, check=False)
        (path / (role + "-metrics-" + label + ".prom")).write_text(result.stdout)
        (path / (role + "-metrics-" + label + ".log")).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError("Native metrics unavailable: " + result.stderr)
