"""Owned lab IO, native protocols and workload processes."""

import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "postgres-cdc-clickhouse"
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
ROLES = ["postgres", "connect"] + [
    f"{p}{i}" for p in ["controller", "broker", "ch", "keeper"] for i in range(1, 4)
]


def command(*args, timeout=600, check=True, data=None):
    result = subprocess.run(
        [*COMPOSE, *args],
        cwd=ROOT,
        text=True,
        input=data,
        capture_output=True,
        timeout=timeout,
    )
    if check and result.returncode:
        raise RuntimeError(result.stdout + "\n" + result.stderr)
    return result


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def wait(predicate, timeout=300):
    end = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < end:
        try:
            if predicate():
                return
        except (
            RuntimeError,
            subprocess.SubprocessError,
            OSError,
            json.JSONDecodeError,
        ) as error:
            last_error = str(error)
        time.sleep(1)
    raise TimeoutError(
        "Timed out: "
        + getattr(predicate, "__name__", "predicate")
        + "; last error: "
        + str(last_error)
    )


def owned(role):
    identifier = command("ps", "--all", "--quiet", role).stdout.strip()
    if not identifier or "\n" in identifier:
        raise RuntimeError("Expected exactly one owned " + role)
    row = json.loads(
        subprocess.check_output(["docker", "inspect", identifier], text=True)
    )[0]
    labels = row["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != role
    ):
        raise RuntimeError("Foreign container")
    return row


def sql(statement, host="ch1"):
    result = command(
        "exec",
        "-T",
        host,
        "clickhouse-client",
        "--user",
        "lab",
        "--password",
        "local-control-only",
        "--query",
        statement,
        "--format",
        "JSONEachRow",
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line]


def pg(statement):
    return command(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-X",
        "-qAt",
        "-U",
        "lab",
        "-d",
        "orders",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        statement,
    ).stdout.strip()


def api(path, method="GET", data=None):
    args = [
        "exec",
        "-T",
        "connect",
        "curl",
        "--silent",
        "--show-error",
        "--max-time",
        "20",
        "--write-out",
        "\n%{http_code}",
        "--request",
        method,
        "http://localhost:8083" + path,
    ]
    if data is not None:
        args += [
            "--header",
            "Content-Type: application/json",
            "--data-binary",
            json.dumps(data),
        ]
    text = command(*args).stdout
    return decode_connect_reply(text)


def decode_connect_reply(text):
    body, status = text.rsplit("\n", 1)
    if not 200 <= int(status) < 300:
        raise RuntimeError(f"Connect HTTP {status}: {body}")
    return json.loads(body) if body.strip() else None


def kafka(*args):
    return command(
        "exec", "-T", "-e", "KAFKA_HEAP_OPTS=-Xms32m -Xmx64m", "broker1", *args
    ).stdout


def topics():
    for name, parts in [
        ("handoff-config", 1),
        ("handoff-offset", 25),
        ("handoff-status", 5),
        ("cdc.public.customers", 1),
        ("cdc.public.orders", 4),
        ("cdc.transaction", 1),
        ("__debezium-heartbeat.cdc", 1),
    ]:
        args = [
            "/opt/kafka/bin/kafka-topics.sh",
            "--bootstrap-server",
            "broker1:9092",
            "--create",
            "--if-not-exists",
            "--topic",
            name,
            "--partitions",
            str(parts),
            "--replication-factor",
            "3",
            "--config",
            "min.insync.replicas=2",
        ]
        if name.startswith("handoff-"):
            args += ["--config", "cleanup.policy=compact"]
        kafka(*args)


def app(mode, path):
    result = command("exec", "-T", "orders-workload", "/orders", mode, timeout=300)
    (path / (mode + "-" + str(time.time_ns()) + ".jsonl")).write_text(
        result.stdout + result.stderr
    )
    return [
        json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")
    ]


def snapshot_counts():
    return sql(
        "SELECT countIf(JSONExtractString(payload,'op')='r') AS reads,countIf(JSONExtractString(payload,'source','snapshot')='last') AS last,countIf(JSONExtractString(payload,'op') IN ('c','u','d')) AS changes FROM pipeline.cdc_history"
    )[0]


def native(path, label):
    state = {
        "utc_ns": time.time_ns(),
        "slot": json.loads(
            pg(
                "SELECT coalesce(json_agg(json_build_object('slot_name',slot_name,'plugin',plugin,'active',active,'restart_lsn',restart_lsn,'confirmed_flush_lsn',confirmed_flush_lsn,'wal_status',wal_status)),'[]') FROM pg_replication_slots"
            )
        ),
        "wal_lsn": pg("SELECT pg_current_wal_lsn()"),
        "connector": api("/connectors/handoff/status"),
    }
    save(path / (label + ".json"), state)
    return state


def phase(value):
    target = ROOT / "results/phase"
    pending = target.with_suffix(".next")
    pending.write_text(value)
    pending.replace(target)


def actor(role, run, path):
    output = (path / (role + ".jsonl")).open("x")
    child = subprocess.Popen(
        [
            *COMPOSE,
            "exec",
            "-T",
            "-e",
            "RUN_ID=" + run,
            "-e",
            "DURATION_S=600",
            "orders-workload",
            "/orders",
            role,
        ],
        cwd=ROOT,
        stdout=output,
        stderr=subprocess.STDOUT,
    )
    return child, output


def events(path):
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.startswith("{")
    ]


def acks(path):
    return [r for r in events(path / "writer.jsonl") if r.get("event") == "ack"]


def fixture():
    return json.loads((ROOT / "results/fixture.json").read_text())
