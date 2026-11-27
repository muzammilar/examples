"""Owned Compose/API boundaries for the order CDC pipeline."""

import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "orders-cdc-analytics"
COMPOSE = ["docker", "compose", "--project-name", PROJECT]
ROLES = [
    *[f"fe{i}" for i in range(1, 4)],
    *[f"be{i}" for i in range(1, 4)],
    *[f"controller{i}" for i in range(1, 4)],
    *[f"broker{i}" for i in range(1, 4)],
    "postgres",
    "connect",
    "jobmanager",
    "taskmanager",
    "orders",
]


def command(*args, timeout=900):
    result = subprocess.run(
        [*COMPOSE, *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"Compose exited {result.returncode}: {result.stdout}\n{result.stderr}"
        )
    return result.stdout


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def directory(mode):
    path = ROOT / "results" / (mode + "-" + str(time.time_ns()))
    path.mkdir(parents=True)
    save(path / "status.json", {"passed": False, "mode": mode})
    if (ROOT / "results/fixture.json").exists():
        save(path / "fixture.json", fixture())
    return path


def fixture():
    return json.loads((ROOT / "results/fixture.json").read_text())


def workload(mode, path):
    value = fixture()
    output = command(
        "exec",
        "-T",
        "-e",
        "RUN_ID=" + value["id"],
        "orders",
        "/orders",
        mode,
        timeout=1000,
    )
    (path / (mode + ".log")).write_text(output)
    return [json.loads(line) for line in output.splitlines()]


def decode_api_response(text, missing_ok=False):
    body, status = text.rsplit("\n", 1)
    code = int(status)
    if code == 404 and missing_ok:
        return None
    if not 200 <= code < 300:
        raise RuntimeError(f"Native HTTP {code}: {body}")
    return json.loads(body) if body else None


def api(path, *, connect=False, method="GET", data=None, missing_ok=False):
    endpoint = "http://connect:8083" if connect else "http://jobmanager:8081"
    args = [
        "exec",
        "-T",
        "connect",
        "curl",
        "--noproxy",
        "*",
        "--connect-timeout",
        "2",
        "--max-time",
        "20",
        "-sS",
        "-X",
        method,
    ]
    if data is not None:
        args += [
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            json.dumps(data),
        ]
    text = command(*args, "--write-out", "\n%{http_code}", endpoint + path, timeout=30)
    return decode_api_response(text, missing_ok)


def wait_for(predicate, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    raise TimeoutError("Declared readiness/recovery deadline exceeded")


def kafka(*args):
    return command(
        "exec",
        "-T",
        "-e",
        "KAFKA_HEAP_OPTS=-Xms32m -Xmx64m",
        "broker1",
        "/opt/kafka/bin/kafka-topics.sh",
        "--bootstrap-server",
        "broker1:9092",
        *args,
    )


def assert_owned(identifier, role=None):
    row = json.loads(
        subprocess.check_output(["docker", "inspect", identifier], text=True)
    )[0]
    labels = row["Config"]["Labels"]
    if labels.get("com.docker.compose.project") != PROJECT or (
        role and labels.get("com.docker.compose.service") != role
    ):
        raise RuntimeError("Refusing an unrelated container")
    return row


def native(path):
    save(path / "checkpoints.json", api("/jobs/" + fixture()["job"] + "/checkpoints"))
    save(
        path / "checkpoint-config.json",
        api("/jobs/" + fixture()["job"] + "/checkpoints/config"),
    )
    save(path / "job.json", api("/jobs/" + fixture()["job"]))
    save(path / "workers.json", api("/taskmanagers"))
    save(path / "connector.json", api("/connectors/orders/status", connect=True))
    topics = kafka("--describe")
    (path / "topics.txt").write_text(topics)
    for topic in (
        "cdc.public.orders",
        "cdc.public.customers",
        "__debezium-heartbeat.cdc",
        "pipeline-config",
        "pipeline-offset",
        "pipeline-status",
    ):
        records = []
        for line in topics.splitlines():
            fields = dict(
                field.strip().split(":", 1)
                for field in line.split("\t")
                if ":" in field
            )
            if fields.get("Topic", "").strip() == topic:
                records.append({key: value.strip() for key, value in fields.items()})
        if (
            not records
            or "min.insync.replicas=2" not in records[0].get("Configs", "")
            or records[0].get("ReplicationFactor") != "3"
            or len(records) != 1 + int(records[0].get("PartitionCount", "0"))
        ):
            raise RuntimeError("Native topic minISR2 required: " + topic)
        for row in records[1:]:
            copies = set(row.get("Replicas", "").split(","))
            if (
                len(copies) != 3
                or set(row.get("Isr", "").split(",")) != copies
                or row.get("Leader") not in copies
            ):
                raise RuntimeError("Native topic RF3/full ISR required: " + topic)
    # Read actual native membership and replica versions; do not infer RF from Compose.
    import topology

    topology.verify(path)
    save(
        path / "containers.json",
        [
            {
                "name": row["Name"],
                "image": row["Image"],
                "memory_limit": row["HostConfig"]["Memory"],
                "nano_cpus": row["HostConfig"]["NanoCpus"],
                "oom_killed": row["State"]["OOMKilled"],
            }
            for identifier in command("ps", "--all", "--quiet").split()
            for row in [assert_owned(identifier)]
        ],
    )
