"""Bounded, project-scoped Compose operations and durable local evidence."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "results"
PROJECT = "kafka-connect-postgres-enrichment"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml")]
BASE = (
    [f"kafka-controller-{n}" for n in range(1, 4)]
    + [f"kafka-broker-{n}" for n in range(1, 4)]
    + ["postgres", "connect-1", "connect-2", "processor"]
)


def environment(extra=None):
    path = STATE / "ports.json"
    ports = json.loads(path.read_text()) if path.exists() else {}
    return (
        os.environ
        | ports
        | {"LAB_UID": str(os.getuid()), "LAB_GID": str(os.getgid())}
        | (extra or {})
    )


def call(args, *, timeout=90, stdin=None, extra=None):
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=environment(extra),
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"{args}: exit={result.returncode}: {result.stderr[-4000:]} {result.stdout[-1000:]}"
        )
    return result.stdout


def compose(*args, **kwargs):
    return call(COMPOSE + list(args), **kwargs)


def save(path, data, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise FileExistsError(path)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    with temporary.open("x") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def inspect(identity):
    (row,) = json.loads(call(["docker", "inspect", identity]))
    labels = row["Config"].get("Labels", {})
    if labels.get("com.docker.compose.project") != PROJECT or labels.get(
        "com.docker.compose.project.working_dir"
    ) != str(ROOT):
        raise RuntimeError("container is outside the owned Compose example")
    return row


def service(name):
    if name not in BASE:
        raise ValueError("unknown infrastructure role")
    identities = compose("ps", "--all", "--quiet", name).splitlines()
    if len(identities) != 1:
        raise RuntimeError("missing or ambiguous service " + name)
    row = inspect(identities[0])
    if row["Config"]["Labels"]["com.docker.compose.service"] != name:
        raise RuntimeError("service identity mismatch")
    return row


def owner():
    receipt = json.loads((STATE / "owner.json").read_text())
    if receipt["project"] != PROJECT or not receipt["owner"]:
        raise RuntimeError("invalid lab ownership receipt")
    return receipt


def wait(check, description, timeout=300):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(1)
    raise RuntimeError(description + " timed out")


def cli(tool, *args):
    brokers = [
        n
        for n in (f"kafka-broker-{i}" for i in range(1, 4))
        if service(n)["State"]["Running"]
    ]
    if not brokers:
        raise RuntimeError("no surviving broker")
    return compose(
        "exec",
        "-T",
        brokers[0],
        "/opt/kafka/bin/" + tool,
        "--bootstrap-server",
        brokers[0] + ":19092",
        *args,
        timeout=90,
    )


def sql(statement):
    return compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-XAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "admin",
        "-d",
        "enrichment",
        "-c",
        statement,
        timeout=20,
    ).strip()


def customers():
    return json.loads(
        sql(
            "SELECT jsonb_object_agg(id,jsonb_build_object('customer_id',id,"
            "'display_name',display_name,'segment',segment)) FROM customers"
        )
    )


def processor_status():
    port = environment()["PROCESSOR_PORT"]
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/status", timeout=3
    ) as response:
        return json.load(response)


def connect_status():
    for role in ("connect-1", "connect-2"):
        if service(role)["State"]["Running"]:
            return json.loads(
                compose(
                    "exec",
                    "-T",
                    role,
                    "curl",
                    "-fsS",
                    "--max-time",
                    "5",
                    "http://localhost:8083/connectors/postgres-customers/status",
                    timeout=15,
                )
            )
    raise RuntimeError("no Connect REST endpoint")


def launch(role, actor, *, output=None):
    if role not in {
        "witness-producer",
        "source-reader",
        "enriched-reader",
        "witness-verifier",
    }:
        raise ValueError("unknown workload role")
    actor = actor.resolve(strict=True)
    if not actor.is_relative_to(STATE.resolve()):
        raise RuntimeError("witness path is outside owned results")
    name = PROJECT + "-" + role + "-" + uuid.uuid4().hex[:12]
    extra = {"CASE_PATH": str(actor)}
    if output is not None:
        output.mkdir(parents=True)
        extra["VERIFICATION_PATH"] = str(output.resolve())
    identity = compose(
        "run", "--detach", "--no-deps", "--name", name, role, extra=extra
    ).strip()
    row = inspect(identity)
    mounts = [m for m in row["Mounts"] if m["Destination"] == "/witness"]
    sources = {str(actor)}
    if sys.platform == "darwin":
        sources.add("/host_mnt" + str(actor))
    if len(mounts) != 1 or mounts[0]["Source"] not in sources:
        raise RuntimeError("workload witness mount mismatch")
    return identity


def completed(identity, root):
    row = inspect(identity)
    if row["State"]["Running"]:
        return False
    save(root / (identity[:12] + "-container.json"), row)
    (root / (identity[:12] + ".log")).write_text(logs(identity))
    if row["State"]["ExitCode"]:
        raise RuntimeError("workload exited nonzero: " + identity)
    return True


def logs(identity):
    inspect(identity)
    return subprocess.run(
        ["docker", "logs", identity],
        cwd=ROOT,
        env=environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=20,
        check=True,
    ).stdout


def fingerprint(topic):
    # One-record negative-control topics retain their known failed physical state.
    output = compose(
        "exec",
        "-T",
        "kafka-broker-1",
        "/opt/kafka/bin/kafka-get-offsets.sh",
        "--bootstrap-server",
        "kafka-broker-1:19092",
        "--topic",
        topic,
        "--time",
        "-1",
    )
    offsets = {
        p: int(offset)
        for _, p, offset in (line.rsplit(":", 2) for line in output.splitlines())
    }
    if set(offsets) != set(map(str, range(6))):
        raise RuntimeError("negative-control offset coverage is incomplete")
    result = subprocess.run(
        COMPOSE
        + [
            "exec",
            "-T",
            "kafka-broker-1",
            "/opt/kafka/bin/kafka-console-consumer.sh",
            "--bootstrap-server",
            "kafka-broker-1:19092",
            "--topic",
            topic,
            "--from-beginning",
            "--max-messages",
            "1",
            "--timeout-ms",
            "10000",
        ],
        cwd=ROOT,
        env=environment(),
        capture_output=True,
        timeout=30,
        check=True,
    )
    import hashlib

    return {
        "end_offsets": offsets,
        "first_value_with_cli_newline_sha256": hashlib.sha256(
            result.stdout
        ).hexdigest(),
    }


def diagnostics(root):
    save(
        root / ("diagnostics-" + uuid.uuid4().hex + ".json"),
        {role: service(role) for role in BASE},
    )
    (root / "services.log").write_text(
        compose("logs", "--no-color", "--tail=150", *BASE, timeout=30)
    )
