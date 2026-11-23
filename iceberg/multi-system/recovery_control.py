"""Witness one local SeaweedFS mini crash without discarding failed state."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
import traceback
from urllib.parse import urlsplit
import uuid

import lab_profiles as lab
from recovery_resources import ResourceSampler, summarize
from recovery_report import operation_summary

ROOT = Path(__file__).resolve().parent
PROJECT, OWNER = lab.PROJECT, lab.OWNER
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    "/dev/null",
    "--project-name",
    PROJECT,
    "-f",
    "docker-compose.yml",
    "-f",
    "config/generated/cluster.yml",
    "-f",
    "docker-compose.recovery.yml",
]
GIB = 1024**3
CAPS = {
    "storage": (1, GIB),
    "clickhouse": (2, 1536 * 1024**2),
    "recovery-tools": (1, 2 * GIB),
    "recovery-writer": (1, GIB),
    "recovery-reader": (1, GIB),
    "recovery-compactor": (2, 2 * GIB),
    "compaction-upload-gate": (1, GIB // 4),
}
DUCKDB_VERSION = os.environ.get("DUCKDB_VERSION", "2.0.1")
if DUCKDB_VERSION not in {"1.5.5", "2.0.1"}:
    raise RuntimeError("DUCKDB_VERSION must be stable 2.0.1 or historical 1.5.5")
if DUCKDB_VERSION == "2.0.1":
    COMPOSE += ["-f", "docker-compose.duckdb2.yml"]
if lab.PROFILE == "current":
    if DUCKDB_VERSION != "2.0.1":
        raise RuntimeError("current lab requires DuckDB 2.0.1")
    for filename in lab.CURRENT_FILES:
        COMPOSE += ["-f", filename]
GO_ROLES = ("recovery-tools", "recovery-writer", "recovery-reader")
BASE_ROLES = ("storage", "clickhouse", *GO_ROLES)
AUXILIARY_ROLES = ("recovery-compactor", "compaction-upload-gate")
GATE = 'trap "exit 143" TERM INT; while ! test -f "$1"; do sleep 0.1; done; shift; exec /usr/local/bin/lab "$@"'
DOCKER_ENV = None
DESKTOP_BIND_TRANSLATION = False


def local_environment():
    # No host cloud credentials/profile variables enter Compose or the workload.
    keys = (
        "PATH",
        "HOME",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "DOCKER_CONTEXT",
        "DOCKER_HOST",
        "DOCKER_CONFIG",
        "DUCKDB_VERSION",
        "LAB_PROFILE",
        "LOCAL_UID",
        "RECOVERY_ROWS",
        "RECOVERY_CUSTOMERS",
        "CUSTOMERS",
        "RECOVERY_BATCH_ROWS",
        "RECOVERY_WRITERS",
        "RECOVERY_TIMEOUT_S",
        "RECOVERY_DELAY_MS",
        "RECOVERY_VISIBILITY_S",
        "RECOVERY_VERIFY_TIMEOUT_S",
    )
    return {key: os.environ[key] for key in keys if key in os.environ}


def call(arguments, *, env=None, timeout=30):
    result = subprocess.run(
        arguments,
        cwd=ROOT,
        env=env if env is not None else DOCKER_ENV,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"{arguments[:3]} failed ({result.returncode}): {result.stdout[-4096:]} {result.stderr[-4096:]}"
        )
    return result.stdout


def local_docker():
    """Pin one inspected Unix context; never change the user's Docker default."""
    global DOCKER_ENV, DESKTOP_BIND_TRANSLATION
    env = local_environment()
    if os.environ.get("DOCKER_TLS_VERIFY") or os.environ.get("DOCKER_CERT_PATH"):
        raise RuntimeError("local Unix Docker context required, without TLS overrides")
    context = call(["docker", "context", "show"], env=env).strip()
    (value,) = json.loads(call(["docker", "context", "inspect", context], env=env))
    endpoint = value["Endpoints"]["docker"]["Host"]
    address = urlsplit(endpoint)
    if (
        not context
        or value["Name"] != context
        or address.scheme != "unix"
        or address.netloc
        or address.query
        or address.fragment
        or not Path(address.path).is_absolute()
        or not stat.S_ISSOCK(Path(address.path).stat().st_mode)
        or (env.get("DOCKER_HOST") and env["DOCKER_HOST"] != endpoint)
    ):
        raise RuntimeError("require one unambiguous local Unix Docker context")
    env.pop("DOCKER_HOST", None)
    env["DOCKER_CONTEXT"] = context
    DOCKER_ENV = env
    info = json.loads(call(["docker", "info", "--format", "{{json .}}"]))
    DESKTOP_BIND_TRANSLATION = (
        sys.platform == "darwin" and info.get("OperatingSystem") == "Docker Desktop"
    )
    if (
        info["OSType"] != "linux"
        or info["NCPU"] < 7
        or info["MemTotal"] < 13 * GIB // 2
    ):
        raise RuntimeError("Linux Docker budget must accommodate 7 CPUs / 6.5 GiB")
    return {
        "context": context,
        "endpoint": endpoint,
        "daemon": {
            key: info.get(key)
            for key in (
                "ID",
                "Name",
                "ServerVersion",
                "Architecture",
                "NCPU",
                "MemTotal",
                "KernelVersion",
                "OperatingSystem",
            )
        },
    }


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def save_text(path, value):
    with path.open("x") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def owned(identifier, expected_service=None):
    (value,) = json.loads(call(["docker", "inspect", identifier]))
    labels = value["Config"].get("Labels", {})
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or Path(labels.get("com.docker.compose.project.working_dir", "/")).resolve()
        != ROOT
    ):
        raise RuntimeError("refuse action on foreign or unproven container")
    if (
        expected_service is not None
        and labels.get("com.docker.compose.service") != expected_service
    ):
        raise RuntimeError("container has the wrong example role")
    return value


def state(value):
    return {
        "id": value["Id"],
        "image_id": value["Image"],
        "state": value["State"],
        "restart_count": value["RestartCount"],
    }


def require_fenced(value):
    if value["State"]["Running"] or value["State"]["Pid"] != 0:
        raise RuntimeError("old process is not positively fenced")
    if value["State"]["ExitCode"] != 137 or value["State"]["OOMKilled"]:
        raise RuntimeError("expected SIGKILL, not another exit or OOM")


def service(name):
    identifiers = call(COMPOSE + ["ps", "--all", "-q", name]).split()
    if len(identifiers) != 1:
        raise RuntimeError("require one owned " + name)
    return owned(identifiers[0], name)


def live_identity(value):
    return (
        value["Id"],
        value["State"]["Pid"],
        value["State"]["StartedAt"],
        value["RestartCount"],
    )


def environment(case):
    if DOCKER_ENV is None:
        raise RuntimeError("local Docker preflight must precede Compose mutations")
    return DOCKER_ENV | {
        "RESULTS_DIR": str(case.relative_to(ROOT)),
        "RECOVERY_RUN_ID": case.name,
    }


def sources():
    paths = [
        ROOT / "Makefile",
        ROOT / "docker-compose.yml",
        ROOT / "docker-compose.recovery.yml",
        ROOT / "config/generated/cluster.yml",
        ROOT / "config/clickhouse.xml",
        ROOT / "config/generated/remote.xml",
        ROOT / "scripts/cluster-config.py",
        ROOT / "recovery_control.py",
        ROOT / "recovery_resources.py",
        ROOT / "recovery_report.py",
        ROOT / "recovery_cleanup.py",
        ROOT / "recovery_retirement.py",
        ROOT / "recovery-contract.json",
        ROOT / "app/Dockerfile",
        ROOT / "app/go.mod",
        ROOT / "app/go.sum",
        *sorted((ROOT / "app").glob("*.go")),
    ]
    optional = [
        ROOT / "lab_profiles.py",
        *[ROOT / name for name in lab.CURRENT_FILES],
        ROOT / "maintenance_control.py",
        ROOT / "duckdb_maintenance.py",
        ROOT / "duckdb_rejections.py",
        ROOT / "duckdb_conflicts.py",
        ROOT / "duckdb_clickhouse.py",
        ROOT / "duckdb_partial.py",
        ROOT / "duckdb_crash.py",
        ROOT / "test_duckdb_clickhouse.py",
        ROOT / "failed_preservation.py",
        ROOT / "failed-preservation-plans.json",
        ROOT / "test_failed_preservation.py",
        ROOT / "test_duckdb_maintenance.py",
        ROOT / "maintenance_smoke.py",
        ROOT / "test_maintenance_control.py",
        ROOT / "prefault_archive.py",
        ROOT / "compound_archive.py",
        ROOT / "test_compound_archive.py",
        ROOT / "cold_storage.py",
        ROOT / "cold_readback.py",
        ROOT / "cold_upgrade.py",
        ROOT / "test_cold_upgrade.py",
        ROOT / "cold_cleanup.py",
        ROOT / "test_cold_cleanup.py",
        ROOT / "test_cold_readback.py",
        ROOT / "docker-compose.cold-readback.yml",
        ROOT / "test_cold_storage.py",
        ROOT / "manifest_control.py",
        ROOT / "test_manifest_control.py",
        ROOT / "docker-compose.duckdb2.yml",
        ROOT / "app/Dockerfile.duckdb2",
        ROOT / "app/go.duckdb2.mod",
        ROOT / "app/go.duckdb2.sum",
        ROOT / "compactor_control.py",
        ROOT / "compactor_evidence.py",
        ROOT / "compactor-contract.json",
    ]
    gate = ROOT / "compaction-upload-gate"
    optional += [
        gate / "Dockerfile",
        gate / "go.mod",
        gate / "go.sum",
        *sorted(gate.glob("*.go")),
    ]
    compactor = ROOT / "iceberg-compaction"
    optional += [compactor / "Dockerfile", *sorted(compactor.glob("*.py"))]
    for path in optional:
        if path.exists():
            if not path.is_file() or not path.resolve().is_relative_to(ROOT.parents[1]):
                raise RuntimeError("auxiliary source escaped this repository")
            paths.append(path)
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def history(path):
    if not path.exists():
        return []
    body = path.read_bytes()
    if body and not body.endswith(b"\n"):
        body = body.rpartition(b"\n")[0]
    return [json.loads(line) for line in body.splitlines()]


def phase(case, name):
    workload = case / ("recovery-" + case.name)
    temporary = workload / ("PHASE-" + uuid.uuid4().hex)
    save_text(temporary, name + "\n")
    os.replace(temporary, workload / "PHASE")
    sync_directory(workload)
    marker = {"phase": name, "host_ns": time.time_ns()}
    with (case / "phases.jsonl").open("a") as stream:
        stream.write(json.dumps(marker) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return marker["host_ns"]


def check_caps(value, role):
    cpus, memory = CAPS[role]
    config = value["HostConfig"]
    if (
        config["NanoCpus"] != cpus * 10**9
        or config["Memory"] != memory
        or config["MemorySwap"] != memory
    ):
        raise RuntimeError(
            "effective CPU/memory/swap caps differ from profile: " + role
        )


def check_networks(value, network_ids):
    actual = {
        network["NetworkID"]
        for network in value["NetworkSettings"]["Networks"].values()
    }
    if not actual or not actual <= network_ids:
        raise RuntimeError("container is outside the owned internal network")


def host_bind_source(source):
    # Docker Desktop can report either host paths or its VM's /host_mnt alias,
    # even for different mounts on the same container. Only translate that alias
    # after identifying the actual local Desktop daemon on macOS.
    if DESKTOP_BIND_TRANSLATION and source.startswith("/host_mnt/"):
        source = source.removeprefix("/host_mnt")
    return Path(source).resolve()


def check_mounts(value, case=None):
    binds = [mount for mount in value["Mounts"] if mount["Type"] == "bind"]
    for mount in binds:
        source = host_bind_source(mount["Source"])
        if not source.is_relative_to(ROOT) or ".aws" in source.parts:
            raise RuntimeError("foreign or cloud-credential bind mount")
    if case is not None and (
        len(binds) != 1
        or binds[0]["Destination"] != "/results"
        or host_bind_source(binds[0]["Source"]) != case.resolve()
    ):
        raise RuntimeError("workload must bind only its owned witness directory")


def current_settings(storage, query):
    def get(port, path):
        return call(
            [
                "docker",
                "exec",
                storage["Id"],
                "curl",
                "--noproxy",
                "*",
                "--proto",
                "=http",
                "--connect-timeout",
                "2",
                "--max-time",
                "5",
                "--fail",
                "--silent",
                "--show-error",
                f"http://127.0.0.1:{port}/{path}",
            ]
        )

    version = call(["docker", "exec", storage["Id"], "weed", "version"])
    query_version = call(
        [
            "docker",
            "exec",
            query["Id"],
            "clickhouse-client",
            "--password",
            "local-reader",
            "--query",
            "SELECT version()",
        ]
    ).strip()
    metrics, topology = get(9327, "metrics"), json.loads(get(9333, "dir/status"))
    memory = re.findall(r"^go_gc_gomemlimit_bytes ([^\n]+)", metrics, re.MULTILINE)
    required = {
        "-volume.max=16",
        "-master.volumeSizeLimitMB=256",
        "-s3.readerCacheSizeMB=256",
        "-s3.cacheCapacityMB=0",
    }
    if (
        not re.search(r"\b4\.54\b", version)
        or "5ef99c1d8" not in version
        or query_version != "26.10.10.3"
        or len(memory) != 1
        or float(memory[0]) != 629145600
        or topology.get("Topology", {}).get("Max") != 16
        or not required <= set(storage["Config"]["Cmd"])
    ):
        raise RuntimeError("current native versions or bounded storage settings differ")
    return {
        "storage_version": version.strip(),
        "clickhouse_version": query_version,
        "effective_go_memory_limit_bytes": 629145600,
        "native_volume_slots": 16,
        "reader_budget_MiB": 256,
        "reader_budget_scope": "Explicit native argv; no independent runtime getter",
        "metrics": metrics,
        "topology": topology,
        "observed_ns": time.time_ns(),
    }


def profile(case, storage, query, *, auxiliary=False):
    """Check rendered limits before creating anything; exact clients are checked again behind their gate."""
    config = json.loads(
        call(
            COMPOSE + ["--profile", "production", "config", "--format", "json"],
            env=environment(case),
        )
    )
    services, images = config["services"], {}
    roles = (*BASE_ROLES, *AUXILIARY_ROLES) if auxiliary else BASE_ROLES
    for name in roles:
        cpus, memory = CAPS[name]
        item = services[name]
        if (
            float(item.get("cpus", 0)) != cpus
            or int(item.get("mem_limit", 0)) != memory
            or int(item.get("memswap_limit", 0)) != memory
        ):
            raise RuntimeError(
                "rendered profile caps are missing or different: " + name
            )
        networks = item.get("networks", {})
        if not networks or any(
            not config["networks"][network].get("internal")
            or config["networks"][network].get("external", False)
            for network in networks
        ):
            raise RuntimeError("profile networks must be internal and example-owned")
        (image,) = json.loads(call(["docker", "image", "inspect", item["image"]]))
        images[name] = image["Id"]
        if (
            name in GO_ROLES
            and DUCKDB_VERSION == "2.0.1"
            and image["Config"].get("Labels", {}).get("org.examples.duckdb.version")
            != DUCKDB_VERSION
        ):
            raise RuntimeError(
                "workload image does not identify the selected DuckDB engine"
            )
        if name in (*GO_ROLES, *AUXILIARY_ROLES):
            if image["Config"].get("Labels", {}).get("org.examples.owner") != OWNER:
                raise RuntimeError("workload image lacks this example's owner label")
            env = item.get("environment", {})
            if (
                name in GO_ROLES
                and (
                    str(env.get("WRITERS")) != "2"
                    or str(env.get("RECOVERY_READERS")) != "2"
                )
            ) or str(env.get("AWS_EC2_METADATA_DISABLED")).lower() != "true":
                raise RuntimeError(
                    "initial profile requires exactly two writers/two readers and local-only SDK configuration"
                )
            mounts = item.get("volumes", [])
            if (
                len(mounts) != 1
                or mounts[0].get("type") != "bind"
                or mounts[0].get("target") != "/results"
                or Path(mounts[0]["source"]).resolve() != case.resolve()
            ):
                raise RuntimeError(
                    "rendered client witness mount differs from owned case"
                )
    if len({images[name] for name in GO_ROLES}) != 1:
        raise RuntimeError("all recovery roles must use the same built workload image")
    network_ids = set()
    for value, role in ((storage, "storage"), (query, "clickhouse")):
        check_caps(value, role)
        check_mounts(value)
        if value["Image"] != images[role]:
            raise RuntimeError(
                "running service differs from rendered local image: " + role
            )
        for identifier in value["NetworkSettings"]["Networks"].values():
            (network,) = json.loads(
                call(["docker", "network", "inspect", identifier["NetworkID"]])
            )
            if (
                not network["Internal"]
                or network.get("Labels", {}).get("com.docker.compose.project")
                != PROJECT
            ):
                raise RuntimeError(
                    "running service network is not internal/example-owned"
                )
            network_ids.add(network["Id"])
    for identifier in call(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ]
    ).split():
        current = owned(identifier)
        if current["Id"] not in {storage["Id"], query["Id"]}:
            raise RuntimeError(
                "stop other owned topology/workloads before this separate recovery profile"
            )
    if auxiliary:
        info = json.loads(call(["docker", "info", "--format", "{{json .}}"]))
        if (
            info["OSType"] != "linux"
            or info["NCPU"] < 11
            or info["MemTotal"] < 43 * GIB // 4
        ):
            raise RuntimeError(
                "compactor inspection overlap requires 11 CPUs / 10.75 GiB"
            )
    return {
        "native_settings": current_settings(storage, query)
        if lab.PROFILE == "current"
        else None,
        "images": images,
        "network_ids": network_ids,
        "rendered": config,
        "live_budget": {
            "cpus": 11 if auxiliary else 7,
            "memory_bytes": 43 * GIB // 4 if auxiliary else 13 * GIB // 2,
        },
        "tool_budget": {"cpus": 4, "memory_bytes": 9 * GIB // 2},
    }


@dataclass
class Client:
    role: str
    service: str
    name: str
    invocation: str
    directory: Path
    run_id: str
    identifier: str | None = None
    initial: dict | None = None
    forced: bool = False
    aborted: bool = False


def inspect_client(client):
    value = owned(client.identifier or client.name, client.service)
    labels = value["Config"]["Labels"]
    if (
        labels.get("org.examples.recovery.run") != client.run_id
        or labels.get("org.examples.recovery.invocation") != client.invocation
        or (client.identifier and value["Id"] != client.identifier)
    ):
        raise RuntimeError("client identity or invocation ownership differs")
    return value


def built_source_proof(value):
    expected = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [
            *sorted((ROOT / "app").glob("*.go")),
            ROOT / "app/go.mod",
            ROOT / "app/go.sum",
            ROOT / "app/Dockerfile",
            ROOT / "app/Dockerfile.duckdb2",
            ROOT / "app/go.duckdb2.mod",
            ROOT / "app/go.duckdb2.sum",
        ]
    }
    text = call(
        [
            "docker",
            "exec",
            value["Id"],
            "cat",
            "/usr/local/share/iceberg-lab/sources.sha256",
        ]
    )
    actual = {}
    for line in text.splitlines():
        checksum, name = line.split(maxsplit=1)
        actual[name.removeprefix("*")] = checksum
    if actual != expected:
        raise RuntimeError("built workload sources differ from current app source")
    binary = call(
        ["docker", "exec", value["Id"], "sha256sum", "/usr/local/bin/lab"]
    ).split()
    if len(binary) != 2 or binary[1] != "/usr/local/bin/lab" or len(binary[0]) != 64:
        raise RuntimeError("invalid native workload binary hash")
    int(binary[0], 16)
    return {"sources": actual, "binary_sha256": binary[0]}


def start_client(case, role, service_name, command, extra, clients, sampler, plan):
    if role in clients:
        raise RuntimeError("refuse to replace an earlier client launch: " + role)
    invocation = uuid.uuid4().hex
    name = PROJECT + "-" + case.name[:8] + "-" + role + "-" + invocation[:8]
    directory = case / "processes" / invocation
    directory.mkdir(parents=True)
    client = Client(role, service_name, name, invocation, directory, case.name)
    clients[role] = (
        client  # Register before launch: a CLI timeout is an ambiguous creation.
    )
    save(
        directory / "launch.json",
        {
            "role": role,
            "service": service_name,
            "name": name,
            "command": command,
            "run_id": case.name,
            "invocation": invocation,
            "extra": extra,
            "created_host_ns": time.time_ns(),
        },
    )
    gate = "/results/processes/" + invocation + "/START"
    arguments = COMPOSE + [
        "run",
        "-d",
        "--no-deps",
        "--pull",
        "never",
        "--name",
        name,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--label",
        "org.examples.recovery.run=" + case.name,
        "--label",
        "org.examples.recovery.invocation=" + invocation,
        "--entrypoint",
        "sh",
        "-e",
        "RECOVERY_RUN_ID=" + case.name,
    ]
    for key, value in extra.items():
        arguments += ["-e", key + "=" + str(value)]
    call(
        arguments + [service_name, "-c", GATE, "sh", gate, command],
        env=environment(case),
    )
    value = inspect_client(client)
    client.identifier, client.initial = value["Id"], value
    check_caps(value, service_name)
    check_networks(value, plan["network_ids"])
    check_mounts(value, case)
    if (
        not value["State"]["Running"]
        or value["Image"] != plan["images"][service_name]
        or value["Config"].get("Labels", {}).get("org.examples.owner") != OWNER
    ):
        raise RuntimeError("gated client is not running the expected built image")
    proof = built_source_proof(value)
    save(directory / "started.json", {**state(value), "built_source": proof})
    sampler.bind(role, value["Id"], "/results")
    return client


def release(client):
    pending = client.directory / "START.pending"
    save(pending, {"released_host_ns": time.time_ns()})
    os.replace(pending, client.directory / "START")
    sync_directory(client.directory)


def observe_client(client):
    value = inspect_client(client)
    directory = client.directory / ("observation-" + uuid.uuid4().hex)
    directory.mkdir()
    save(directory / "state.json", state(value))
    result = subprocess.run(
        ["docker", "logs", "--timestamps", value["Id"]],
        cwd=ROOT,
        env=DOCKER_ENV,
        capture_output=True,
        text=True,
        timeout=30,
    )
    save_text(directory / "stdout.log", result.stdout)
    save_text(directory / "stderr.log", result.stderr)
    save(directory / "logs-command.json", {"exit_code": result.returncode})
    if result.returncode:
        raise RuntimeError("could not preserve owned client logs")
    return value, result.stdout, result.stderr


def fenced_client(value):
    return not value["State"]["Running"] and value["State"]["Pid"] == 0


def kill_client(client):
    value = inspect_client(
        client
    )  # Failed inspection never authorizes a kill/fence claim.
    if value["State"]["Running"]:
        client.forced = True
        call(["docker", "kill", "--signal", "SIGKILL", value["Id"]])
        value = inspect_client(client)
        require_fenced(value)
    elif not fenced_client(value):
        raise RuntimeError("client is not positively fenced")
    return value


def abort_client(client):
    """Bounded termination of one registered client, never a service wildcard."""
    client.aborted = True
    value = inspect_client(client)
    if value["State"]["Running"]:
        call(["docker", "kill", "--signal", "SIGTERM", value["Id"]])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            value = inspect_client(client)
            if not value["State"]["Running"]:
                break
            time.sleep(0.2)
    if value["State"]["Running"]:
        value = kill_client(client)
    if not fenced_client(value):
        raise RuntimeError("aborted client is not positively fenced")
    return value


def retain_end(client, value, *, reason=None):
    proof = {
        **state(value),
        "forced": client.forced,
        "aborted": client.aborted,
        "reason": reason,
        "clean": fenced_client(value)
        and value["State"]["ExitCode"] == 0
        and not value["State"]["OOMKilled"]
        and not client.forced
        and not client.aborted
        and client.initial is not None
        and value["State"]["StartedAt"] == client.initial["State"]["StartedAt"]
        and value["RestartCount"] == client.initial["RestartCount"],
    }
    path = client.directory / "ended.json"
    if path.exists():
        prior = json.loads(path.read_text())
        proof["clean"] = proof["clean"] and prior["clean"]
    else:
        save(path, proof)
    return proof


def wait_coverage(sampler, roles, since_ns, actors=None, timeout=45):
    deadline = time.monotonic() + timeout
    while True:
        if actors:
            check_actors(actors)
        if sampler.errors:
            raise RuntimeError("resource sampler stopped: " + repr(sampler.errors))
        try:
            sampler.require_coverage(roles, since_ns)
            return
        except (RuntimeError, FileNotFoundError) as error:
            if isinstance(error, RuntimeError) and not str(error).startswith(
                "missing healthy-phase resource coverage"
            ):
                raise
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "healthy-phase native resource coverage missing"
                ) from error
        time.sleep(0.25)


def tool(
    case,
    command,
    clients,
    sampler,
    plan,
    *,
    verification=None,
    diagnostic=False,
    timeout=1800,
    inspection=None,
):
    if inspection is not None:
        allowed = {
            "RECOVERY_OBSERVATION_ID",
            "RECOVERY_INSPECTION_PHASE",
            "RECOVERY_PIN_OBSERVATION",
            "RECOVERY_CANDIDATE_URI",
        }
        if (
            command != "recovery-inspect"
            or not isinstance(inspection, dict)
            or not set(inspection) <= allowed
            or verification is not None
            or diagnostic
        ):
            raise RuntimeError("inspection arguments require the read-only inspector")
        identifier = inspection.get("RECOVERY_OBSERVATION_ID")
        try:
            parsed = uuid.UUID(identifier)
        except (ValueError, AttributeError, TypeError) as error:
            raise RuntimeError("canonical inspection UUID required") from error
        if parsed.version != 4 or str(parsed) != identifier:
            raise RuntimeError("canonical inspection UUID required")
        stage = inspection.get("RECOVERY_INSPECTION_PHASE")
        pin, candidate = (
            inspection.get("RECOVERY_PIN_OBSERVATION"),
            inspection.get("RECOVERY_CANDIDATE_URI"),
        )
        if (
            stage not in {"before", "candidate", "interrupted", "recovered"}
            or (stage == "before") != (pin is None)
            or (stage == "candidate") != (candidate is not None)
        ):
            raise RuntimeError("inspection arguments differ from phase")
        if pin is not None:
            try:
                parsed_pin = uuid.UUID(pin)
            except (ValueError, AttributeError, TypeError) as error:
                raise RuntimeError(
                    "canonical pinned observation UUID required"
                ) from error
            if parsed_pin.version != 4 or str(parsed_pin) != pin:
                raise RuntimeError("canonical pinned observation UUID required")
        if candidate is not None and (
            not isinstance(candidate, str)
            or not candidate.startswith("s3://warehouse/")
            or not candidate.endswith(".parquet")
            or any(ord(char) < 32 for char in candidate)
        ):
            raise RuntimeError("owned Parquet candidate URI required")
    elif command == "recovery-inspect":
        raise RuntimeError("inspection arguments required")
    for client in clients.values():
        value = inspect_client(client)
        if value["State"]["Running"] and not (
            command == "recovery-inspect"
            and client.service in {"recovery-writer", "recovery-reader"}
            and client.initial is not None
            and live_identity(value) == live_identity(client.initial)
        ):
            raise RuntimeError("one-shot tool cannot overlap another live client")
    role = "tool-" + uuid.uuid4().hex
    extra = dict(inspection or {})
    if verification:
        extra["RECOVERY_VERIFICATION_ID"] = verification
    if diagnostic:
        extra["RECOVERY_DIAGNOSTIC"] = "true"
    client, started = None, time.time_ns()
    try:
        client = start_client(
            case, role, "recovery-tools", command, extra, clients, sampler, plan
        )
        wait_coverage(sampler, [role], started)
        release(client)
        deadline = time.monotonic() + timeout
        while True:
            value = inspect_client(client)
            if not value["State"]["Running"]:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("one-shot client deadline exceeded: " + command)
            time.sleep(0.25)
        value, stdout, stderr = observe_client(client)
        retain_end(client, value)
        if not fenced_client(value) or value["State"]["OOMKilled"]:
            raise RuntimeError("one-shot did not end fenced without OOM")
        if value["State"]["ExitCode"] != 0 and not diagnostic:
            raise RuntimeError("one-shot failed: " + command + " " + stderr[-2048:])
        if diagnostic and value["State"]["ExitCode"] == 0:
            raise RuntimeError(
                "diagnostic invocation must retain a failing exit status"
            )
        return stdout
    except BaseException:
        client = client or clients.get(role)
        try:
            if client is not None:
                value = abort_client(client)
                observe_client(client)
                retain_end(client, value, reason="one-shot failed/deadline")
        except BaseException as cleanup:
            try:
                save(
                    client.directory / ("cleanup-error-" + uuid.uuid4().hex + ".json"),
                    {"error": repr(cleanup)},
                )
            except BaseException:
                print(
                    "Could not persist one-shot cleanup failure: " + repr(cleanup),
                    file=sys.stderr,
                )
        raise
    finally:
        original = sys.exc_info()[1]
        try:
            sampler.unbind(role)
        except BaseException as error:
            if original:
                original.add_note("Sampler unbind also failed: " + repr(error))
            else:
                raise


def check_actors(actors):
    for name, client in actors.items():
        value = inspect_client(client)
        if not value["State"]["Running"] or live_identity(value) != live_identity(
            client.initial
        ):
            raise RuntimeError("live actor exited or restarted: " + name)


def progress(case, actors, name, timeout=180):
    workload = case / ("recovery-" + case.name)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_actors(actors)
        complete = True
        for actor in actors:
            events = history(workload / (actor + ".jsonl"))
            if any(
                event.get("result") in {"mismatch", "visibility_timeout"}
                for event in events
            ):
                raise RuntimeError("reader contract failure: " + actor)
            matching = [
                event
                for event in events
                if event.get("phase") == event.get("ended_phase") == name
                and event.get("result") == "ok"
            ]
            complete &= (
                len(matching) >= 2
                if actor.startswith("writer")
                else {event.get("locality") for event in matching}
                >= {"recent", "historical"}
            )
        if complete:
            return
        time.sleep(0.5)
    raise RuntimeError("no independent read/write progress in " + name)


def stop_actors(case, actors, sampler=None, timeout=45):
    """Request normal journal shutdown, then attempt every exact client even if another fails."""
    workload = case / ("recovery-" + case.name)
    errors, stop_failed = [], False
    try:
        if workload.exists():
            with (workload / "STOP").open("a") as stream:
                stream.flush()
                os.fsync(stream.fileno())
            sync_directory(workload)
    except BaseException as error:
        stop_failed = True
        errors.append({"role": "STOP-witness", "error": repr(error)})
    deadline = time.monotonic() + timeout
    for name, client in actors.items():
        try:
            while True:
                value = inspect_client(client)
                if not value["State"]["Running"]:
                    break
                if stop_failed or not (client.directory / "START").exists():
                    value = abort_client(client)
                    break
                if time.monotonic() >= deadline:
                    value = kill_client(client)
                    break
                time.sleep(0.2)
            value, _, _ = observe_client(client)
            proof = retain_end(
                client, value, reason="deadline SIGKILL" if client.forced else None
            )
            if not proof["clean"]:
                raise RuntimeError("client did not stop cleanly: " + name)
        except BaseException as error:
            errors.append({"role": name, "error": repr(error)})
        finally:
            if sampler:
                try:
                    sampler.unbind(name)
                except BaseException as error:
                    errors.append({"role": name + "-metrics", "error": repr(error)})
    if errors:
        raise RuntimeError("client cleanup failures: " + json.dumps(errors))


def wait_storage(identifier, plan=None):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        current = owned(identifier, "storage")
        if (
            current["State"]["Running"]
            and current["State"].get("Health", {}).get("Status") == "healthy"
        ):
            if plan:
                check_caps(current, "storage")
                check_networks(current, plan["network_ids"])
                check_mounts(current)
            return current
        time.sleep(0.5)
    raise RuntimeError(
        "native catalog did not become healthy after retained-volume restart"
    )


def hold_outage(actors, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_actors(actors)
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))


def data_volume(value):
    names = [
        mount["Name"]
        for mount in value["Mounts"]
        if mount["Type"] == "volume" and mount["Destination"] == "/data"
    ]
    if len(names) != 1:
        raise RuntimeError("ambiguous owned storage volume")
    return names[0]


def verification_proof(case, identifier, *, diagnostic=False):
    path = (
        case
        / ("recovery-" + case.name)
        / "verifications"
        / identifier
        / "verified.json"
    )
    value = json.loads(path.read_text())
    config = case / ("recovery-" + case.name) / "config.json"
    if (
        value["run_id"] != case.name
        or value["verification_id"] != identifier
        or value["config_sha256"] != hashlib.sha256(config.read_bytes()).hexdigest()
        or value["diagnostic"] != diagnostic
        or not value["integrity_passed"]
    ):
        raise RuntimeError("verification identity/config/integrity differs")
    if not diagnostic and (
        not all(
            value[key] is True
            for key in (
                "passed",
                "qualified",
                "actor_lifecycle_passed",
                "strict_mutation_history_passed",
                "reader_contract_passed",
            )
        )
        or value["qualification_issues"]
    ):
        raise RuntimeError("native verification did not qualify")
    if diagnostic and value["qualified"]:
        raise RuntimeError("diagnostic export must never qualify")
    return {
        "verification_id": identifier,
        "verification_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def finish_metrics(case, sampler):
    records, summary = sampler.close()
    save(case / "resource-summary.json", summary)
    save(case / "operation-summary.json", operation_summary(case))
    marks = history(case / "phases.jsonl")
    save(
        case / "phase-resources.json",
        {
            left["phase"]: summarize(
                [
                    row
                    for row in records
                    if left["host_ns"] <= row["host_started_ns"] < right["host_ns"]
                ]
            )
            for left, right in zip(marks, marks[1:])
        },
    )


def storage_crash():
    context = local_docker()
    results_root = ROOT / lab.RESULTS_SUBDIR
    base = lab.validate_results(
        os.environ.get("RESULTS_DIR", lab.DEFAULT_RESULTS), ROOT
    )
    if results_root.is_symlink() or not base.is_relative_to(results_root.resolve()):
        raise RuntimeError(
            "RESULTS_DIR must stay inside this example's ignored results/"
        )
    if shutil.disk_usage(ROOT).free < 8 * GIB:
        raise RuntimeError("need 8 GiB free before a new witnessed recovery case")
    storage, query = service("storage"), service("clickhouse")
    if not all(
        value["State"]["Running"]
        and value["State"].get("Health", {}).get("Status") == "healthy"
        for value in (storage, query)
    ):
        raise RuntimeError("healthy storage and query services required")
    volume_name = data_volume(storage)
    (volume,) = json.loads(call(["docker", "volume", "inspect", volume_name]))
    if volume.get("Labels", {}).get("com.docker.compose.project") != PROJECT:
        raise RuntimeError("foreign data volume")
    case = base / "production" / str(uuid.uuid4())
    plan = profile(case, storage, query)
    case.mkdir(parents=True)
    save(
        case / "case.json",
        {
            "kind": "iceberg-storage-crash",
            "lab_profile": lab.PROFILE,
            "duckdb_version": DUCKDB_VERSION,
            "run_id": case.name,
            "project": PROJECT,
            "context": context,
            "volume": {key: volume[key] for key in ("Name", "CreatedAt", "Mountpoint")},
            "storage": state(storage),
            "query": state(query),
            "source_sha256": sources(),
            "created_ns": time.time_ns(),
            "scope": "Whole SeaweedFS mini process outage: S3 and catalog fail together. Retained-volume, one-host functional recovery; not HA, disk/power loss or capacity.",
        },
    )
    save(case / "profile.json", plan | {"network_ids": sorted(plan["network_ids"])})
    # Register the fixture durably before any catalog or table mutation. Cleanup
    # must detect a missing case directory instead of forgetting its table.
    from recovery_cleanup import register_fixture

    register_fixture(case)
    sampler = ResourceSampler(case, env=DOCKER_ENV)
    sampler.bind("storage", storage["Id"], "/data")
    sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
    sampler.start()
    clients, actors, primary, errors, proof = {}, {}, None, [], None
    fault_attempted = False
    try:
        print(tool(case, "recovery-seed", clients, sampler, plan), end="", flush=True)
        workload = case / ("recovery-" + case.name)
        config = json.loads((workload / "config.json").read_text())
        if config["writers"] != 2 or config["readers"] != 2:
            raise RuntimeError(
                "initial profile requires exactly two writers and two readers"
            )
        baseline = phase(case, "baseline")
        for index in range(2):
            role = f"writer-{index:03d}"
            actors[role] = start_client(
                case,
                role,
                "recovery-writer",
                "recovery-write",
                {"RECOVERY_WRITER_ID": index},
                clients,
                sampler,
                plan,
            )
            release(actors[role])
        for index, engine in enumerate(("duckdb", "clickhouse")):
            role = f"reader-{index:03d}"
            actors[role] = start_client(
                case,
                role,
                "recovery-reader",
                "recovery-read",
                {"RECOVERY_READER_ID": index, "RECOVERY_READ_ENGINE": engine},
                clients,
                sampler,
                plan,
            )
            release(actors[role])
        progress(case, actors, "baseline")
        hold_outage(actors, 10)
        wait_coverage(sampler, ["storage", "clickhouse", *actors], baseline, actors)
        phase(case, "fault")
        before = owned(storage["Id"], "storage")
        save(case / "before-kill.json", state(before))
        if (
            live_identity(before) != live_identity(storage)
            or data_volume(before) != volume_name
        ):
            raise RuntimeError("storage changed before the planned fault")
        fault_attempted = True  # CLI failure may still have delivered the kill.
        call(["docker", "kill", "--signal", "SIGKILL", storage["Id"]])
        stopped = owned(storage["Id"], "storage")
        save(case / "after-kill.json", state(stopped))
        require_fenced(stopped)
        hold_outage(actors, 20)
        phase(case, "recovery")
        call(["docker", "start", storage["Id"]])
        current = wait_storage(storage["Id"], plan)
        if (
            data_volume(current) != volume_name
            or current["State"]["StartedAt"] == storage["State"]["StartedAt"]
            or current["RestartCount"] != storage["RestartCount"]
        ):
            raise RuntimeError("retained-volume restart identity differs")
        save(case / "reopened.json", state(current))
        recovered = phase(case, "recovered")
        progress(case, actors, "recovered")
        hold_outage(actors, 10)
        wait_coverage(sampler, ["storage", "clickhouse", *actors], recovered, actors)
        if live_identity(owned(query["Id"], "clickhouse")) != live_identity(query):
            raise RuntimeError("unrelated query survivor restarted")
        phase(case, "stopping")
        stop_actors(case, actors, sampler)
        verification = str(uuid.uuid4())
        print(
            tool(
                case,
                "recovery-verify",
                clients,
                sampler,
                plan,
                verification=verification,
            ),
            end="",
            flush=True,
        )
        proof = verification_proof(case, verification)
        final_query = owned(query["Id"], "clickhouse")
        save(case / "query-survivor-final.json", state(final_query))
        if live_identity(final_query) != live_identity(query):
            raise RuntimeError("query survivor changed during final verification")
    except BaseException:
        primary = sys.exc_info()
        try:
            save(
                case / "failure.json",
                {
                    "passed": False,
                    "error": repr(primary[1]),
                    "traceback": traceback.format_exc(),
                    "native_storage_preserved": True,
                },
            )
        except BaseException as error:
            errors.append({"role": "failure-recorder", "error": repr(error)})
    finally:
        if primary and fault_attempted:
            try:
                current = owned(storage["Id"], "storage")
                save(case / "cleanup-storage.json", state(current))
                if not current["State"]["Running"]:
                    require_fenced(current)
                    if data_volume(current) != volume_name:
                        raise RuntimeError("storage volume changed before rescue")
                    call(["docker", "start", storage["Id"]])
                    wait_storage(storage["Id"], plan)
            except BaseException as error:
                errors.append({"role": "storage-rescue", "error": repr(error)})
        # Include launch attempts which failed before being added to actors.
        try:
            stop_actors(case, clients, sampler)
        except BaseException as error:
            errors.append({"role": "clients", "error": repr(error)})
        if primary:
            if not isinstance(primary[1], (KeyboardInterrupt, SystemExit)):
                try:
                    current = owned(storage["Id"], "storage")
                    if (
                        not current["State"]["Running"]
                        or current["State"].get("Health", {}).get("Status") != "healthy"
                    ):
                        raise RuntimeError(
                            "diagnostic export blocked: storage not healthy"
                        )
                    diagnostic = str(uuid.uuid4())
                    tool(
                        case,
                        "recovery-verify",
                        clients,
                        sampler,
                        plan,
                        verification=diagnostic,
                        diagnostic=True,
                    )
                    save(
                        case / "diagnostic-export.json",
                        verification_proof(case, diagnostic, diagnostic=True),
                    )
                except BaseException as error:
                    errors.append({"role": "diagnostic-export", "error": repr(error)})
        try:
            if (case / ("recovery-" + case.name)).exists():
                phase(case, "finished")
        except BaseException as error:
            errors.append({"role": "phase-recorder", "error": repr(error)})
        try:
            finish_metrics(
                case, sampler
            )  # Always stop/join even if a phase write failed.
        except BaseException as error:
            errors.append({"role": "resources", "error": repr(error)})
        if errors:
            try:
                save(case / "cleanup-errors.json", errors)
            except BaseException as error:
                if primary:
                    primary[1].add_note(
                        "Could not persist cleanup errors: " + repr(error)
                    )
                else:
                    errors.append({"role": "cleanup-recorder", "error": repr(error)})
    if primary:
        if errors:
            primary[1].add_note(
                "Cleanup/diagnostic/resource failures recorded in cleanup-errors.json"
            )
        raise primary[1].with_traceback(primary[2])
    try:
        if errors:
            raise RuntimeError("cleanup/metrics incomplete; preserve all owned storage")
        if sources() != json.loads((case / "case.json").read_text())["source_sha256"]:
            raise RuntimeError("runtime sources changed during the experiment")
    except BaseException as error:
        try:
            save(
                case / "failure.json",
                {
                    "passed": False,
                    "error": repr(error),
                    "traceback": traceback.format_exc(),
                    "native_storage_preserved": True,
                },
            )
        except BaseException as recording:
            error.add_note(
                "Could not persist final qualification failure: " + repr(recording)
            )
        raise
    save(
        case / "result.json", {"passed": True, **proof, "completed_ns": time.time_ns()}
    )
    print(str(case.relative_to(ROOT)), flush=True)


def interrupt_controller(signum, _frame):
    # Turn termination into an exception so the case's existing finally block
    # restores storage and fences its workload processes before exit.
    raise KeyboardInterrupt("controller received " + signal.Signals(signum).name)


def main():
    if sys.version_info < (3, 11):
        raise RuntimeError("recovery tests require Python 3.11 or newer")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["storage-crash"])
    parser.parse_args()
    signal.signal(signal.SIGTERM, interrupt_controller)
    lock_path = ROOT / "results/controller.lock"
    lock_path.parent.mkdir(exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        storage_crash()


if __name__ == "__main__":
    main()
