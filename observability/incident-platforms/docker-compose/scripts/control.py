"""Local native-platform setup and witnessed driver controls; no cloud discovery."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "obs-incident-platforms"
ALL_BACKENDS = [
    "openobserve",
    "openobserve-sync",
    "clickstack-clickhouse",
    "clickstack-mongo",
    "clickstack-app",
    "clickstack-collector",
    "signoz-clickhouse",
    "signoz-keeper",
    "signoz-postgres",
    "signoz-app",
    "signoz-collector",
]
SERVICE = os.environ.get("OPENOBSERVE_SERVICE", "openobserve-sync")
if SERVICE not in ["openobserve", "openobserve-sync"]:
    raise ValueError("Unknown local OpenObserve variant")
os.environ.setdefault("LAB_UID", str(os.getuid()))
ENDPOINT = subprocess.check_output(
    [
        "docker",
        "context",
        "inspect",
        "--format",
        '{{(index .Endpoints "docker").Host}}',
    ],
    text=True,
).strip()
if not ENDPOINT.startswith("unix://"):
    raise RuntimeError("This example requires a local Unix Docker socket")
os.environ.pop("DOCKER_CONTEXT", None)
DOCKER = ["docker", "--host", ENDPOINT]
COMPOSE = [*DOCKER, "compose", "--project-name", PROJECT]


def command(*args, check=True):
    result = subprocess.run(
        [*COMPOSE, *args], cwd=ROOT, text=True, capture_output=True, timeout=660
    )
    if check and result.returncode:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result


def tool(*args, check=True):
    return command("run", "--rm", "--no-deps", "incident-workload", *args, check=check)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".next")
    with temporary.open("w") as file:
        json.dump(value, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def owned(role):
    identifier = command("ps", "--all", "--quiet", role).stdout.strip()
    if not identifier:
        raise RuntimeError("Missing owned role: " + role)
    row = json.loads(
        subprocess.check_output([*DOCKER, "inspect", identifier], text=True)
    )[0]
    labels = row["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != role
        or labels.get("com.docker.compose.project.working_dir") != str(ROOT)
    ):
        raise RuntimeError("Foreign container")
    return row


def active_backend():
    row = owned(SERVICE)
    if (
        row["HostConfig"]["NanoCpus"] != 8000000000
        or row["HostConfig"]["Memory"] != 12 * 2**30
    ):
        raise RuntimeError("Active backend budget differs")
    if not row["State"]["Running"]:
        raise RuntimeError("Requested native variant is not running")
    for rival in ALL_BACKENDS:
        if rival != SERVICE and command("ps", "--quiet", rival).stdout.strip():
            raise RuntimeError(
                "Two backend variants are running; endpoint/budget ambiguous"
            )
    expected = "true" if SERVICE == "openobserve" else "false"
    value = dict(v.split("=", 1) for v in row["Config"]["Env"] if "=" in v).get(
        "ZO_WAL_FSYNC_DISABLED", "true"
    )
    if value != expected:
        raise RuntimeError("Effective WAL fsync variant differs")
    return row


def retired(case):
    registry = ROOT / "results/retired-cases.json"
    if not registry.exists():
        return False
    receipt = json.loads(registry.read_text()).get(case.name)
    if receipt is None:
        return False
    proof = json.loads((ROOT / receipt).read_text())
    if not proof.get("owned_resources_absent"):
        raise RuntimeError("Retired case lacks verified cleanup")
    for name, expected in proof["witnesses"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Retired witness changed: " + name)
    return True


def source_manifest(path):
    response = tool("--mode=manifest")
    manifest = {}
    for line in response.stdout.splitlines():
        digest, name = line.split(maxsplit=1)
        if (
            hashlib.sha256((ROOT / "incident-workload" / name).read_bytes()).hexdigest()
            != digest
        ):
            raise RuntimeError("Workload image/source differs: " + name)
        manifest[name] = digest
    expected = {p.name for p in (ROOT / "incident-workload").glob("*.go")} | {
        "go.mod",
        "go.sum",
    }
    if set(manifest) != expected:
        raise RuntimeError("Workload image omitted or added source files")
    save(path / "source.json", manifest)


def bootstrap():
    info = json.loads(
        subprocess.check_output([*DOCKER, "info", "--format", "{{json .}}"], text=True)
    )
    if info["NCPU"] < 10 or info["MemTotal"] < 16 * 2**30:
        raise RuntimeError("Need 10 CPU / 16 GiB including workload and reserve")
    capacity = json.loads(tool("--mode=filesystem").stdout)
    save(ROOT / "results/filesystem.json", capacity)
    if capacity["filesystem_available_bytes"] < 8 * 2**30:
        raise RuntimeError("Fewer than 8 GiB free in local Docker storage")
    save(
        ROOT / "results/environment.json",
        {
            k: info.get(k)
            for k in [
                "NCPU",
                "MemTotal",
                "KernelVersion",
                "ServerVersion",
                "Architecture",
                "OperatingSystem",
                "Driver",
            ]
        },
    )
    if command(
        "ps", "--quiet", "incident-workload", "incident-publisher", "incident-reader"
    ).stdout.strip():
        raise RuntimeError("Live actors prevent changing the backend variant")
    for role in ALL_BACKENDS[2:]:
        if command("ps", "--quiet", role).stdout.strip():
            raise RuntimeError(
                "Stop/reconcile ClickStack before changing the OpenObserve platform"
            )
    for rival in ALL_BACKENDS:
        if rival != SERVICE:
            if command("ps", "--all", "--quiet", rival).stdout.strip():
                owned(rival)
            command("stop", rival)
            if command("ps", "--all", "--quiet", rival).stdout.strip():
                stopped = owned(rival)
                save(
                    ROOT
                    / "results/preserved-variants"
                    / (rival + "-" + uuid.uuid4().hex + ".json"),
                    {
                        "id": stopped["Id"],
                        "state": stopped["State"],
                        "mounts": stopped["Mounts"],
                        "scope": "Stopped only; data and original failed cases retained, no destruction receipt",
                    },
                )
    configuration = json.loads(
        command(
            "--profile",
            "openobserve",
            "--profile",
            "openobserve-buffered",
            "config",
            "--format",
            "json",
        ).stdout
    )
    reference = configuration["services"][SERVICE]["image"]
    if subprocess.run(
        [*DOCKER, "image", "inspect", reference], capture_output=True
    ).returncode:
        anonymous = ROOT / ".build/anonymous-docker"
        anonymous.mkdir(parents=True, exist_ok=True)
        (anonymous / "config.json").write_text("{}\n")
        with (ROOT / "results" / ("pull-" + SERVICE + ".log")).open("w") as log:
            subprocess.run(
                [DOCKER[0], "--config", str(anonymous), *DOCKER[1:], "pull", reference],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=660,
            )
    command("up", "--detach", "--pull", "never", SERVICE)
    until = time.monotonic() + 120
    while time.monotonic() < until:
        response = tool("--mode=health", check=False)
        with (ROOT / "results/openobserve-health.log").open("a") as log:
            log.write(response.stdout + "\n" + response.stderr)
        if response.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError("OpenObserve health did not become ready")
    row = owned(SERVICE)
    if (
        row["HostConfig"]["NanoCpus"] != 8000000000
        or row["HostConfig"]["Memory"] != 12 * 2**30
    ):
        raise RuntimeError("Effective OpenObserve budget differs")
    if not row["HostConfig"]["ReadonlyRootfs"] or "ALL" not in (
        row["HostConfig"].get("CapDrop") or []
    ):
        raise RuntimeError("Effective root filesystem/capability restrictions differ")
    save(
        ROOT / "results/environments" / (SERVICE + "-" + uuid.uuid4().hex + ".json"),
        {
            "id": row["Id"],
            "image": row["Image"],
            "reference": row["Config"]["Image"],
            "service": SERVICE,
            "wal_fsync_disabled": dict(
                v.split("=", 1) for v in row["Config"]["Env"] if "=" in v
            ).get("ZO_WAL_FSYNC_DISABLED", "true"),
            "budget": {
                "cpu": row["HostConfig"]["NanoCpus"],
                "memory": row["HostConfig"]["Memory"],
            },
            "mounts": row["Mounts"],
        },
    )
    source_manifest(ROOT / "results")


def driver_control():
    active_backend()
    if any(
        not retired(p.parent)
        and json.loads(p.read_text()).get("service", "openobserve") == SERVICE
        for p in (ROOT / "results/cases").glob("*/case.json")
    ):
        raise RuntimeError(
            "Preserved publication exists; inspect/reconcile it before new input"
        )
    identifier = uuid.uuid4().hex
    path = ROOT / "results/cases" / identifier
    fixture = {
        "run": identifier,
        "payload_seed": uuid.uuid4().hex,
        "start_ms": int(time.time() - 7200) * 1000,
        "requests": 100,
        "logs_per_request": 100,
        "metric_points": 100,
    }
    save(path / "fixture.json", fixture)
    save(
        path / "case.json",
        {
            "kind": "driver-control",
            "backend": "openobserve",
            "service": SERVICE,
            "qualification": "pending native schema and independent oracle",
        },
    )
    source_manifest(path)
    response = tool(
        "--mode=publish",
        "--backend=openobserve",
        "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
        "--output=/" + str((path / "openobserve").relative_to(ROOT)),
        check=False,
    )
    (path / "publisher.log").write_text(response.stdout + "\n" + response.stderr)
    if response.returncode:
        raise RuntimeError("Driver publication failed; preserve original journals")
    summaries = [
        json.loads(line) for line in response.stdout.splitlines() if line.strip()
    ]
    if len(summaries) != 3 or any(
        not r["completed"] or r["unacknowledged_requests"] for r in summaries
    ):
        save(
            path / "status.json",
            {
                "publication_exited_zero": True,
                "qualified": False,
                "scope": "Healthy driver requests were not all accepted; preserve native rejection before any repair",
            },
        )
        raise RuntimeError("Healthy driver admission failed; preserve journals")
    schema = tool("--mode=schema", check=False)
    (path / "schema.jsonl").write_text(schema.stdout)
    (path / "schema.log").write_text(schema.stderr)
    save(
        path / "status.json",
        {
            "publication_exited_zero": True,
            "schema_exit": schema.returncode,
            "qualified": False,
            "scope": "Schema inspection only; independent exact-body readback still required",
        },
    )
    if schema.returncode:
        raise RuntimeError("Native schema inspection failed")
    print(identifier)


def verify_control():
    identifier = os.environ.get("CASE", "")
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ValueError("Invalid preserved case ID")
    path = ROOT / "results/cases" / identifier
    active_backend()
    if retired(path):
        raise RuntimeError(
            "Case was verified and retired; its live data no longer exists"
        )
    if (
        json.loads((path / "case.json").read_text()).get("service", "openobserve")
        != SERVICE
    ):
        raise RuntimeError("Preserved case belongs to a different data volume/variant")
    if json.loads((path / "case.json").read_text())["backend"] != "openobserve":
        raise RuntimeError("Wrong backend")
    if command(
        "ps", "--quiet", "incident-workload", "incident-publisher", "incident-reader"
    ).stdout.strip():
        raise RuntimeError("A live workload prevents freezing journals")
    directory = path / "openobserve"
    hashes = {
        signal: hashlib.sha256(
            (directory / signal / "requests.jsonl").read_bytes()
        ).hexdigest()
        for signal in ["metrics", "logs", "traces"]
    }
    proof = directory / "fenced.json"
    if proof.exists():
        if json.loads(proof.read_text())["histories"] != hashes:
            raise RuntimeError("Previously fenced histories changed")
    else:
        save(
            proof,
            {
                "actors_stopped": True,
                "histories": hashes,
                "fenced_at_unix_s": time.time(),
            },
        )
    destination = path / "readbacks" / uuid.uuid4().hex
    source_manifest(destination)
    report = destination / "readback.json"
    response = tool(
        "--mode=verify",
        "--backend=openobserve",
        "--fixture=/" + str((path / "fixture.json").relative_to(ROOT)),
        "--output=/" + str(directory.relative_to(ROOT)),
        "--report=/" + str(report.relative_to(ROOT)),
        check=False,
    )
    (destination / "readback.log").write_text(response.stdout + "\n" + response.stderr)
    save(
        destination / "status.json",
        {
            "common_projection_passed": response.returncode == 0,
            "lossless_otlp_roundtrip": False,
            "scope": "Uncached native SQL plus frozen acknowledgement history; declared trace projection limits remain",
        },
    )
    if response.returncode:
        raise RuntimeError("Native readback failed; preserve report and original data")
    print(destination)


def query_screen():
    from experiments import running_actors

    active_backend()
    if running_actors():
        raise RuntimeError("Query screening requires an idle owned workload")
    selected = []
    kinds = set()
    for metadata in (ROOT / "results/cases").glob("*/case.json"):
        value = json.loads(metadata.read_text())
        if retired(metadata.parent) or value.get("service", "openobserve") != SERVICE:
            continue
        if value["kind"] in ["healthy", "ingress-crash"]:
            status = metadata.parent / "status.json"
            if not status.exists() or not json.loads(status.read_text()).get(
                "common_projection_passed"
            ):
                raise RuntimeError("Unqualified native case blocks query ranking")
            kinds.add(value["kind"])
            if value["kind"] == "healthy":
                selected.append(metadata.parent)
    if kinds != {"healthy", "ingress-crash"}:
        raise RuntimeError("Both healthy and crash-recovery gates are required")
    case = max(selected, key=lambda p: p.stat().st_mtime_ns)
    directory = ROOT / "results/query-screens" / uuid.uuid4().hex
    source_manifest(directory)
    save(
        directory / "configuration.json",
        {
            "service": SERVICE,
            "fixture": str((case / "fixture.json").relative_to(ROOT)),
            "trials": 3,
            "warm_repeats": 10,
            "scope": "Within-profile bounded diagnostics; cross-platform alternating trials and winner not qualified",
        },
    )
    for trial in range(3):
        (ROOT / "results/phase").write_text(
            "incident-query:" + SERVICE + ":" + str(trial)
        )
        report = directory / ("trial-" + str(trial) + ".json")
        result = tool(
            "--mode=queries",
            "--backend=openobserve",
            "--fixture=/" + str((case / "fixture.json").relative_to(ROOT)),
            "--report=/" + str(report.relative_to(ROOT)),
            check=False,
        )
        (directory / ("trial-" + str(trial) + ".log")).write_text(
            result.stdout + "\n" + result.stderr
        )
        if result.returncode:
            raise RuntimeError("Native incident query failed; preserve diagnostic")
    print(directory)


def main():
    (ROOT / "results").mkdir(exist_ok=True)
    with (ROOT / "results/controller.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        mode = sys.argv[1]
        (ROOT / "results/phase").write_text(mode)
        platform = os.environ.get("PLATFORM", "openobserve")
        if platform not in ["openobserve", "clickstack", "signoz"]:
            raise ValueError("Unknown isolated platform")
        if platform == "signoz" and mode != "check":
            from signoz import execute

            execute(mode)
            return
        if platform == "clickstack" and mode != "check":
            from clickstack import execute

            execute(mode)
            return
        if mode == "check":
            command(
                "--profile",
                "openobserve",
                "--profile",
                "openobserve-buffered",
                "--profile",
                "clickstack",
                "--profile",
                "signoz",
                "--profile",
                "tools",
                "config",
                "--quiet",
            )
            build = command("build", "incident-workload")
            (ROOT / "results/build.log").write_text(build.stdout + "\n" + build.stderr)
            print("Compose validated; workload vet/tests/build passed inside Docker")
        elif mode == "bootstrap":
            bootstrap()
        elif mode == "driver-control":
            driver_control()
        elif mode == "verify-control":
            verify_control()
        elif mode in ["full", "ingress-crash"]:
            from experiments import run

            run("healthy" if mode == "full" else mode)
        elif mode in ["up", "test", "down", "docker-down", "preserve-stop"]:
            from lifecycle import execute

            execute(mode)
        elif mode == "queries":
            query_screen()
        elif mode == "status":
            print(command("ps", "--all").stdout)
        else:
            raise ValueError(mode)


if __name__ == "__main__":
    main()
