"""Native IO scoped to this local margin pipeline."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "mysql-flink-clickhouse-airflow"
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
os.environ.setdefault("LAB_UID", str(os.getuid()))
BASE_ROLES = ["mysql", "airflow-db"] + [
    f"{name}{i}"
    for name in ["controller", "broker", "ch", "keeper"]
    for i in range(1, 4)
]
APPLICATION_ROLES = [
    "connect",
    "jobmanager",
    "taskmanager1",
    "taskmanager2",
    "airflow-api",
    "airflow-scheduler",
    "airflow-dag-processor",
    "orders-workload",
]
ROLES = BASE_ROLES + APPLICATION_ROLES


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
    pending = path.with_suffix(path.suffix + ".next")
    pending.write_text(json.dumps(value, indent=2) + "\n")
    pending.replace(path)


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


def decode_http_reply(text):
    body, status = text.rsplit("\n", 1)
    if not 200 <= int(status) < 300:
        raise RuntimeError(f"HTTP {status}: {body}")
    return json.loads(body) if body.strip() else None


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
    return decode_http_reply(text)


def kafka(*args):
    return command(
        "exec", "-T", "-e", "KAFKA_HEAP_OPTS=-Xms32m -Xmx64m", "broker1", *args
    ).stdout


def phase(value):
    target = ROOT / "results/phase"
    pending = target.with_suffix(".next")
    pending.write_text(value)
    pending.replace(target)


def events(path):
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.startswith("{")
    ]


def fixture():
    return json.loads((ROOT / "results/fixture.json").read_text())


def mysql(statement):
    return command(
        "exec",
        "-T",
        "-e",
        "MYSQL_PWD=local-root-only",
        "mysql",
        "mysql",
        "--user=root",
        "--batch",
        "--raw",
        "--skip-column-names",
        "orders",
        "--execute",
        statement,
    ).stdout.strip()


def metadata(statement):
    return command(
        "exec",
        "-T",
        "airflow-db",
        "psql",
        "-XqAt",
        "-U",
        "airflow",
        "-d",
        "airflow",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        statement,
    ).stdout.strip()


def flink(path):
    raw = command(
        "exec",
        "-T",
        "jobmanager",
        "curl",
        "-sS",
        "--max-time",
        "15",
        "--write-out",
        "\n%{http_code}",
        "http://localhost:8081" + path,
    ).stdout
    return decode_http_reply(raw)


def app(mode, run, path):
    output = command(
        "exec",
        "-T",
        "-e",
        "RUN_ID=" + run,
        "orders-workload",
        "/orders",
        mode,
        check=False,
        timeout=360,
    )
    (path / (mode + "-" + str(time.time_ns()) + ".jsonl")).write_text(
        output.stdout + output.stderr
    )
    if output.returncode:
        raise RuntimeError(output.stdout + output.stderr)
    rows = [
        json.loads(line) for line in output.stdout.splitlines() if line.startswith("{")
    ]
    manifests = [r["manifest"] for r in rows if r.get("event") == "source"]
    expected = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for pattern in ["*.go", "go.mod", "go.sum"]
        for p in (ROOT / "orders-workload").glob(pattern)
    }
    if len(manifests) != 1:
        raise RuntimeError("Missing actual actor build manifest")
    actual = {line.split()[1]: line.split()[0] for line in manifests[0].splitlines()}
    if actual != expected:
        raise RuntimeError("Actor build differs from checkout; rebuild before mutation")
    return rows


def tool(role, path, timeout=300):
    if role not in ["state-init", "airflow-init", "sql-client"]:
        raise ValueError("Unknown owned tool role")
    result = command("run", "--rm", role, check=False, timeout=timeout)
    (path / (role + "-counters.txt")).write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    if role == "sql-client" and "[ERROR]" in result.stdout:
        raise RuntimeError(
            "SQL CLI reported an error despite exit zero; inspect its preserved output"
        )
    if (
        "CGROUP cpu.stat" not in result.stdout
        or "CGROUP memory.peak" not in result.stdout
    ):
        raise RuntimeError("One-shot tool did not preserve pre-exit resource counters")


def actor(mode, run, path):
    stream = (path / (mode + ".jsonl")).open("x")
    child = subprocess.Popen(
        [
            *COMPOSE,
            "exec",
            "-T",
            "-e",
            "RUN_ID=" + run,
            "orders-workload",
            "/orders",
            mode,
        ],
        cwd=ROOT,
        stdout=stream,
        stderr=subprocess.STDOUT,
    )
    return child, stream


def case(kind):
    run = uuid.uuid4().hex
    path = ROOT / "results/cases" / run
    save(path / "case.json", {"fixture": fixture()["id"], "run": run, "kind": kind})
    manifest = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for pattern in [
            "scripts/*.py",
            "orders-workload/*.go",
            "sql/*",
            "config/*",
            "dags/*",
            "compose.yaml",
            "Makefile",
        ]
        for p in ROOT.glob(pattern)
        if p.is_file()
    }
    save(path / "source-manifest.json", manifest)
    return run, path


def topics(path):
    entries = [
        ("margins-config", 1),
        ("margins-offset", 25),
        ("margins-status", 5),
        ("margins-schema-history", 1),
        ("cdc.orders.customers", 1),
        ("cdc.orders.orders", 4),
        ("cdc.orders.debezium_signal", 1),
        ("cdc", 1),
        ("cdc.notifications", 1),
        ("cdc.transaction", 1),
        ("__debezium-heartbeat.cdc", 1),
        ("margins.enriched", 4),
        ("margins.isolation", 1),
    ]
    for name, parts in entries:
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
        if name in ["margins-config", "margins-offset", "margins-status"]:
            args += ["--config", "cleanup.policy=compact"]
        if name == "margins-schema-history":
            args += ["--config", "retention.ms=-1", "--config", "retention.bytes=-1"]
        kafka(*args)
    (path / "topics.txt").write_text(
        kafka(
            "/opt/kafka/bin/kafka-topics.sh",
            "--bootstrap-server",
            "broker1:9092",
            "--describe",
        )
    )


def source_counts():
    return sql(
        "SELECT topic,countIf(JSONExtractString(payload,'op')='r') AS reads,countIf(JSONExtractString(payload,'op') IN ('c','u','d')) AS changes FROM pipeline.history WHERE topic IN ('cdc.orders.orders','cdc.orders.customers') GROUP BY topic"
    )


def native(path, label):
    value = {
        "source": json.loads(
            mysql(
                "SELECT JSON_OBJECT('version',VERSION(),'gtid',@@GLOBAL.gtid_executed,'sync_binlog',@@GLOBAL.sync_binlog,'flush_commit',@@GLOBAL.innodb_flush_log_at_trx_commit,'binlog_format',@@GLOBAL.binlog_format,'binlog_row_image',@@GLOBAL.binlog_row_image)"
            )
        ),
        "connector": api("/connectors/margins/status"),
        "source_offsets": api("/connectors/margins/offsets"),
        "input_counts": source_counts(),
    }
    job = json.loads((ROOT / "results/job.json").read_text())["id"]
    value["job"] = flink("/jobs/" + job)
    value["checkpoints"] = flink("/jobs/" + job + "/checkpoints")
    save(path / (label + ".json"), value)
    return value
