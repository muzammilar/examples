"""Independent role crashes and owned empty-disk rebuilds; never reset the oracle."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from production_observations import summarize

system, mode = sys.argv[1:]
if system not in ("starrocks", "clickhouse") or mode not in (
    "process",
    "disk",
    "leader",
):
    raise ValueError("fault.py starrocks|clickhouse process|disk|leader")
project = os.environ["COMPOSE_PROJECT_NAME"]
compose = ["docker", "compose", "-f", "docker-compose.json"]
root = Path("results")


def run(*args, capture=False):
    result = subprocess.run(
        list(args), check=False, text=True, capture_output=capture, timeout=300
    )
    if result.returncode:
        if capture:
            with (root / (case + "-commands.log")).open("a") as log:
                log.write(result.stdout + result.stderr)
            raise RuntimeError(
                f"command failed ({result.returncode}): {args}; {result.stderr[-6000:]}"
            )
        result.check_returncode()
    return result.stdout


def tool(action, *args, host=None):
    options = ["-e", "CH_HOST=" + host] if host else []
    return run(
        *compose, "run", "--rm", "-T", *options, "client", action, *args, capture=True
    )


def metadata(query):
    return json.loads(tool("metadata", query))


def query(sql, host=None):
    return json.loads(tool("sql", "clickhouse", sql, host=host))


def execute(sql, host):
    tool("exec", "clickhouse", sql, host=host)


target = "sr-be-0" if system == "starrocks" else "ch00"
if mode == "leader":
    if system == "starrocks":
        leaders = [
            r["IP"]
            for r in metadata("SHOW FRONTENDS")
            if r["Role"] == "LEADER" and r["Alive"] == "true"
        ]
    else:
        leaders = []
        for i in range(3):
            text = run(
                *compose,
                "exec",
                "-T",
                f"keeper{i}",
                "clickhouse-keeper-client",
                "--host",
                "127.0.0.1",
                "--port",
                "9181",
                "--query",
                "stat",
                capture=True,
            )
            if "Mode: leader" in text:
                leaders.append(f"keeper{i}")
    if len(leaders) != 1:
        raise RuntimeError("could not identify one native leader")
    target = leaders[0]
if os.environ.get("FAULT_SERVICE"):
    allowed = ("sr-be-1",) if system == "starrocks" else ("ch01",)
    if mode != "process" or os.environ["FAULT_SERVICE"] not in allowed:
        raise ValueError("alternate process target must be sr-be-1 or ch01")
    target = os.environ["FAULT_SERVICE"]
case = f"{system}-{mode}-{target}-" + datetime.now(timezone.utc).strftime(
    "%Y%m%dT%H%M%S"
)
owned_ids = run(*compose, "ps", "-q", capture=True).split()
owned = json.loads(run("docker", "inspect", *owned_ids, capture=True))
survivors = {
    c["Config"]["Labels"]["com.docker.compose.service"]: (
        c["Id"],
        c["State"]["StartedAt"],
    )
    for c in owned
    if c["Config"]["Labels"]["com.docker.compose.service"] not in (target, "client")
}
cid = run(*compose, "ps", "-q", target, capture=True).strip()
info = json.loads(run("docker", "inspect", cid, capture=True))[0]
if (
    info["Config"]["Labels"].get("com.docker.compose.project") != project
    or info["Config"]["Labels"].get("com.docker.compose.service") != target
):
    raise RuntimeError("refusing foreign fault target")
disk = None
if mode == "disk":
    destination = "/data" if system == "starrocks" else "/var/lib/clickhouse"
    disk = next(
        m["Name"]
        for m in info["Mounts"]
        if m["Destination"] == destination and m["Type"] == "volume"
    )
    volume = json.loads(run("docker", "volume", "inspect", disk, capture=True))[0]
    if (
        volume["Labels"].get("com.docker.compose.project") != project
        or volume["Labels"].get("com.docker.compose.volume") != target
    ):
        raise RuntimeError("refusing foreign disk")


def records():
    path = root / ("fault-" + case + ".jsonl")
    return (
        [json.loads(line) for line in path.read_text().split("\n")[:-1]]
        if path.exists()
        else []
    )


def progress(after=None):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if load.poll() is not None:
            raise RuntimeError("producer exited before native recovery")
        acks = [
            r
            for r in records()
            if r["stage"] == "ack"
            and (
                after is None
                or datetime.fromisoformat(r["at"].replace("Z", "+00:00")) > after
            )
        ]
        if len(acks) >= 3:
            return
        time.sleep(1)
    raise RuntimeError("no three new acknowledged batches within deadline")


def read_progress(after=None):
    deadline = time.monotonic() + 120
    path = root / ("reads-" + case + ".jsonl")
    while time.monotonic() < deadline:
        if reader.poll() is not None:
            raise RuntimeError("reader exited before native recovery")
        events = (
            [json.loads(line) for line in path.read_text().split("\n")[:-1]]
            if path.exists()
            else []
        )
        if (
            sum(
                e["outcome"] == "ok"
                and (
                    after is None
                    or datetime.fromisoformat(e["at"].replace("Z", "+00:00")) > after
                )
                for e in events
            )
            >= 3
        ):
            return
        time.sleep(1)
    raise RuntimeError("no three successful live reads within deadline")


def wait_native():
    versions = {}
    if system == "starrocks":
        for table in ("orders", "customers", "recovery"):
            versions[table] = int(
                metadata("SHOW PARTITIONS FROM orders_lab." + table)[0][
                    "VisibleVersion"
                ]
            )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if system == "starrocks":
            front = metadata("SHOW FRONTENDS")
            back = metadata("SHOW BACKENDS")
            good = (
                len(front) == 3
                and all(r["Alive"] == "true" for r in front)
                and sum(r["Role"] == "LEADER" for r in front) == 1
                and len(back) == 3
                and all(r["Alive"] == "true" for r in back)
            )
            for table in ("orders", "customers", "recovery"):
                tablets = metadata("SHOW TABLET FROM orders_lab." + table)
                version = versions[table]
                good = (
                    good
                    and len(tablets) == 9
                    and all(
                        r["State"] == "NORMAL" and int(r["Version"]) >= version
                        for r in tablets
                    )
                )
        else:
            good = True
            for shard in range(3):
                for replica in range(3):
                    values = query(
                        "SELECT count() FROM system.replicas WHERE database='orders_lab' AND (total_replicas!=3 OR active_replicas!=3 OR queue_size>0 OR is_readonly OR is_session_expired) LIMIT 1",
                        f"ch{shard}{replica}",
                    )
                    good = good and values == [["0"]]
        if good:
            if system == "starrocks":
                try:
                    tool("replica-health")
                except RuntimeError:
                    time.sleep(2)
                    continue
            return
        time.sleep(2)
    raise RuntimeError("native roles/replicas/repair queues did not converge")


log = (root / (case + "-load.log")).open("w")
reader_log = (root / (case + "-reader.log")).open("w")
markers = [("before", datetime.now(timezone.utc))]
load_name = project + "-" + case
load = subprocess.Popen(
    [
        *compose,
        "run",
        "--rm",
        "--name",
        load_name,
        "-e",
        "FAULT_SYSTEM=" + system,
        "-e",
        "FAULT_CASE=" + case,
        "client",
        "churn",
    ],
    stdout=log,
    stderr=subprocess.STDOUT,
)
reader_name = load_name + "-reader"
reader = subprocess.Popen(
    [
        *compose,
        "run",
        "--rm",
        "--name",
        reader_name,
        "-e",
        "FAULT_SYSTEM=" + system,
        "-e",
        "FAULT_CASE=" + case,
        "observer",
        "watch",
    ],
    stdout=reader_log,
    stderr=subprocess.STDOUT,
)
try:
    progress()
    read_progress()
    producer_image = json.loads(run("docker", "inspect", load_name, capture=True))[0][
        "Image"
    ]
    begin = time.monotonic()
    injected = datetime.now(timezone.utc)
    markers.append(("outage", injected))
    run(*compose, "kill", "--signal", "SIGKILL", target)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        stopped = json.loads(run("docker", "inspect", cid, capture=True))[0]
        if not stopped["State"]["Running"]:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("target did not stop; refusing disk replacement")
    down_at = datetime.now(timezone.utc)
    if disk:
        run(*compose, "rm", "--force", target)
        run("docker", "volume", "rm", disk)
    time.sleep(30)
    progress(after=down_at + timedelta(seconds=10))
    if mode == "disk" and system == "clickhouse":
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            active = query(
                "SELECT count() FROM system.zookeeper WHERE path='/orders_lab/0/orders/replicas/0' AND name='is_active' LIMIT 1",
                "ch01",
            )
            if active == [["0"]]:
                break
            time.sleep(2)
        else:
            raise RuntimeError("old replica still has an active native Keeper session")
        for table in ("orders", "recovery", "customers"):
            execute(
                f"SYSTEM DROP REPLICA '0' FROM ZKPATH '/orders_lab/0/{table}'", "ch01"
            )
    restored_at = datetime.now(timezone.utc)
    markers.append(("recovery", restored_at))
    run(*compose, "up", "-d", "--wait", "--wait-timeout", "180", target)
    if disk and system == "clickhouse":
        execute("CREATE DATABASE IF NOT EXISTS orders_lab", "ch00")
        for table in (
            "orders_local",
            "recovery_local",
            "customers_local",
            "orders",
            "recovery",
        ):
            ddl = query("SHOW CREATE TABLE orders_lab." + table, "ch01")[0][0]
            if "orders_lab." + table not in ddl:
                raise RuntimeError("unexpected native schema during replica recovery")
            if "Replicated" in ddl and "'{replica}'" not in ddl:
                raise RuntimeError(
                    "native replica DDL did not preserve its replica macro"
                )
            execute(ddl, "ch00")
    if disk and system == "starrocks":
        for table in ("orders", "customers", "recovery"):
            tool("exec", "starrocks", "ADMIN REPAIR TABLE orders_lab." + table)
    wait_native()
    recovered = time.monotonic() - begin
    after = datetime.now(timezone.utc)
    markers.append(("after", after))
    progress(after=after)
    read_progress(after=after)
    (root / ("stop-" + case)).touch()
    if load.wait(timeout=60) != 0:
        raise RuntimeError("producer failed")
    if reader.wait(timeout=60) != 0:
        raise RuntimeError("reader failed")
    observations = summarize(
        root / ("fault-" + case + ".jsonl"),
        root / ("reads-" + case + ".jsonl"),
        markers,
        datetime.now(timezone.utc),
    )
    (root / (case + "-observations.json")).write_text(
        json.dumps(observations, indent=2) + "\n"
    )
    if observations["contract_failures"]:
        raise RuntimeError(str(observations["contract_failures"]))
    verifier_image = json.loads(
        run("docker", "image", "inspect", project + "-client:dev", capture=True)
    )[0]["Id"]
    tool("verify")
    current_ids = run(*compose, "ps", "-q", capture=True).split()
    current_containers = json.loads(
        run("docker", "inspect", *current_ids, capture=True)
    )
    current_survivors = {
        c["Config"]["Labels"]["com.docker.compose.service"]: (
            c["Id"],
            c["State"]["StartedAt"],
        )
        for c in current_containers
        if c["Config"]["Labels"]["com.docker.compose.service"] in survivors
    }
    if current_survivors != survivors:
        raise RuntimeError(
            "an unrelated survivor restarted during a single-target fault"
        )
    current = json.loads(
        run(
            "docker",
            "inspect",
            run(*compose, "ps", "-q", target, capture=True).strip(),
            capture=True,
        )
    )[0]
    if current["State"]["StartedAt"] == info["State"]["StartedAt"]:
        raise RuntimeError("target process did not restart")
    report = {
        "case": case,
        "observations": observations,
        "target": target,
        "producer_image": producer_image,
        "verifier_image": verifier_image,
        "survivors_unchanged": len(survivors),
        "injected_at": injected.isoformat(),
        "down_seconds": (restored_at - down_at).total_seconds(),
        "acks_during_outage_after_10s_grace": sum(
            r["count"]
            for r in records()
            if r["stage"] == "ack"
            and down_at + timedelta(seconds=10)
            < datetime.fromisoformat(r["at"].replace("Z", "+00:00"))
            < restored_at
        ),
        "removed_volume": disk,
        "scope": "empty owned durable data-volume replacement"
        if disk
        else "SIGKILL with retained volume",
        "recovery_seconds_including_30s_hold": recovered,
        "final_verified_seconds": time.monotonic() - begin,
        "acknowledged_rows": sum(r["count"] for r in records() if r["stage"] == "ack"),
        "ambiguous_batches": sum(r["stage"] == "ambiguous" for r in records()),
        "reconciliation": json.loads((root / "recovery-history.json").read_text()),
    }
    (root / (case + "-result.json")).write_text(json.dumps(report, indent=2) + "\n")
except Exception as error:
    (root / (case + "-failure.json")).write_text(
        json.dumps({"case": case, "status": "failed", "error": str(error)}, indent=2)
        + "\n"
    )
    raise
finally:
    (root / ("stop-" + case)).touch()
    for process, name in ((load, load_name), (reader, reader_name)):
        if process.poll() is None:
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "stop", "--timeout", "5", name], check=False)
                process.wait(timeout=30)
    log.close()
    reader_log.close()
