"""Budgeted native deployment and guarded, evidence-preserving teardown."""

import json
import os
import subprocess
import uuid

from lab import (
    ROOT,
    PROJECT,
    BASE_ROLES,
    ROLES,
    app,
    api,
    case,
    command,
    fixture,
    flink,
    metadata,
    mysql,
    phase,
    owned,
    save,
    sql,
    tool,
    topics,
    wait,
)


def workload_counts():
    orders = int(os.environ.get("ORDER_COUNT", "1000000"))
    customers = int(os.environ.get("CUSTOMER_COUNT", "10000"))
    if not 1000000 <= orders <= 2000000 or customers != 10000:
        raise ValueError(
            "This oracle requires 1,000,000..2,000,000 orders and exactly 10,000 customers"
        )
    return orders, customers


def bootstrap(path):
    workload_counts()
    if (ROOT / "results/fixture.json").exists():
        raise RuntimeError(
            "Fixture exists: diagnose/verify retained state instead of reseeding"
        )
    config = json.loads(command("config", "--format", "json").stdout)
    budget = {
        "cpus": sum(float(s.get("cpus", 0)) for s in config["services"].values()),
        "memory_bytes": sum(
            int(s.get("mem_limit", 0)) for s in config["services"].values()
        ),
        "reserve_bytes": 2 * 1024**3,
    }
    host = json.loads(
        subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
    )
    if (
        budget["cpus"] > host["NCPU"]
        or budget["memory_bytes"] + budget["reserve_bytes"] > host["MemTotal"]
    ):
        raise RuntimeError(
            "Combined declared budget, including tool roles, exceeds the host reserve"
        )
    save(path / "budget.json", budget)
    command("pull", *BASE_ROLES, "connect", timeout=900)
    image = config["services"]["airflow-db"]["image"]
    raw = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cpus",
            ".1",
            "--memory",
            "32m",
            "--entrypoint",
            "df",
            image,
            "-PB1",
            "/",
        ],
        text=True,
    )
    free = int(raw.splitlines()[-1].split()[3])
    if free < 8 * 1024**3:
        raise RuntimeError("Less than 8 GiB free after builds/pulls; refuse startup")
    save(
        path / "disk-preflight.json", {"free_bytes": free, "minimum_bytes": 8 * 1024**3}
    )
    state = {"id": uuid.uuid4().hex, "stage": "provisioning"}
    save(ROOT / "results/fixture.json", state)
    phase("infrastructure")
    command("up", "-d", "--wait", *BASE_ROLES, timeout=900)
    tool("state-init", path)
    topics(path)
    tool("airflow-init", path)
    command(
        "up", "-d", "--wait", *[r for r in ROLES if r not in BASE_ROLES], timeout=600
    )
    wait(lambda: int(flink("/overview")["slots-total"]) >= 2, 180)
    wait(
        lambda: metadata(
            "SELECT count(*) FROM dag WHERE dag_id IN ('margin_backfill','margin_reconciliation')"
        )
        == "2",
        180,
    )
    for name in ["margin_backfill", "margin_reconciliation"]:
        command("exec", "-T", "airflow-scheduler", "airflow", "dags", "unpause", name)
    state["stage"] = "infrastructure"
    save(ROOT / "results/fixture.json", state)


def initialize(path):
    workload_counts()
    state = fixture()
    if state["stage"] != "infrastructure":
        raise RuntimeError(
            "Fresh infrastructure required; use retained-state verification"
        )
    state["stage"] = "initializing"
    save(ROOT / "results/fixture.json", state)
    run, seed_path = case("seed")
    app("manifest", run, seed_path)
    from observations import disk_usage

    disk_usage(seed_path, "before-seed")
    phase("source-seed")
    app("seed", run, seed_path)
    orders, customers = workload_counts()
    baseline = json.loads(
        mysql(
            f"SELECT JSON_OBJECT('orders',COUNT(*),'min_id',MIN(id),'max_id',MAX(id),'wrong',SUM("
            f"customer_id<>MOD(id-1,{customers})+1 OR amount_cents<>1000+MOD(id,10000) OR "
            "cost_cents<>600+MOD(id,500) OR revision<>1 OR cancelled<>0 OR event_second<>1700000000+MOD(id,86400))) FROM orders"
        )
    )
    dimensions = json.loads(
        mysql(
            "SELECT JSON_OBJECT('customers',COUNT(*),'min_id',MIN(id),'max_id',MAX(id),'wrong',SUM("
            "CAST(region AS BINARY)<>CAST(CONCAT('r',MOD(id,10)) AS BINARY) OR discount_bps<>MOD(id,6)*100 OR revision<>1)) FROM customers"
        )
    )
    save(
        seed_path / "independent-seed-check.json",
        {"orders": baseline, "customers": dimensions},
    )
    if baseline != {
        "orders": orders,
        "min_id": 1,
        "max_id": orders,
        "wrong": 0,
    } or dimensions != {
        "customers": customers,
        "min_id": 1,
        "max_id": customers,
        "wrong": 0,
    }:
        raise RuntimeError(
            "Generated source differs from its deterministic declared input"
        )
    disk_usage(seed_path, "after-seed")
    for host in ["ch1", "ch2", "ch3"]:
        for name in ["state.sql", "ingest.sql"]:
            command(
                "exec",
                "-T",
                host,
                "clickhouse-client",
                "--user",
                "lab",
                "--password",
                "local-control-only",
                "--multiquery",
                data=(ROOT / "sql" / name).read_text(),
            )
    api(
        "/connectors",
        "POST",
        {
            "name": "margins",
            "config": json.loads((ROOT / "config/connector.json").read_text()),
        },
    )
    tool("sql-client", path, timeout=240)

    def submitted():
        jobs = [j for j in flink("/jobs/overview")["jobs"] if j["state"] == "RUNNING"]
        if len(jobs) != 1:
            return False
        save(ROOT / "results/job.json", {"id": jobs[0]["jid"], "name": jobs[0]["name"]})
        return True

    wait(submitted, 180)
    phase("initial-snapshot")
    wait(
        lambda: int(
            sql(
                "SELECT count() AS n FROM pipeline.history WHERE topic='cdc.notifications' AND JSONExtractString(payload,'aggregate_type')='Initial Snapshot' AND JSONExtractString(payload,'type')='COMPLETED'"
            )[0]["n"]
        )
        > 0,
        300,
    )
    state["stage"] = "ready"
    save(ROOT / "results/fixture.json", state)


def start_dag(name, run, path):
    if (
        name not in ["margin_backfill", "margin_reconciliation"]
        or len(run) != 32
        or any(c not in "0123456789abcdef" for c in run)
    ):
        raise ValueError("Invalid owned DAG identity")
    result = command(
        "exec",
        "-T",
        "airflow-scheduler",
        "airflow",
        "dags",
        "trigger",
        "--run-id",
        run,
        name,
    )
    (path / (name + "-trigger.txt")).write_text(result.stdout + result.stderr)


def resume_submission(path):
    """Continue a failed SQL submission over verified seed data; never seed again."""
    state = fixture()
    if state["stage"] != "initializing":
        raise RuntimeError(
            "Only an interrupted initialization can use SQL submission recovery"
        )
    orders, customers = workload_counts()
    counts = mysql(
        "SELECT (SELECT COUNT(*) FROM orders),(SELECT COUNT(*) FROM customers),(SELECT COUNT(*) FROM receipts)"
    )
    if counts != f"{orders}\t{customers}\t1":
        raise RuntimeError(
            "Expected untouched seeded input is absent; diagnose retained state"
        )
    seeds = []
    for marker in (ROOT / "results/cases").glob("*/case.json"):
        if json.loads(marker.read_text())["fixture"] != state["id"]:
            continue
        journal = marker.parent / "seed.jsonl"
        if journal.exists():
            rows = [json.loads(line) for line in journal.read_text().splitlines()]
            if [row["event"] for row in rows] != ["intent", "ack"]:
                raise RuntimeError("An unresolved seed outcome cannot be resumed")
            seeds.append(rows[-1])
    if (
        len(seeds) != 1
        or seeds[0]["orders"] != orders
        or seeds[0]["customers"] != customers
    ):
        raise RuntimeError("Missing unique durable seed acknowledgement")
    receipt = seeds[0]["receipt"]
    if len(receipt) != 32 or any(c not in "0123456789abcdef" for c in receipt):
        raise RuntimeError("Invalid seed receipt")
    if (
        mysql(
            f"SELECT COUNT(*) FROM receipts WHERE id='{receipt}' AND operation='seed' AND first_id=1 AND last_id={orders} AND affected={orders}"
        )
        != "1"
    ):
        raise RuntimeError("Native atomic seed receipt differs")
    if flink("/jobs/overview")["jobs"]:
        raise RuntimeError("Inspect existing jobs before submitting another join")
    save(
        path / "retained-seed-proof.json",
        {"counts": counts, "ack": seeds[0], "reseeded": False},
    )
    tool("sql-client", path, timeout=240)

    def running():
        jobs = [j for j in flink("/jobs/overview")["jobs"] if j["state"] == "RUNNING"]
        if len(jobs) != 1:
            return False
        save(ROOT / "results/job.json", {"id": jobs[0]["jid"], "name": jobs[0]["name"]})
        return True

    wait(running, 180)
    wait(
        lambda: int(
            sql(
                "SELECT count() AS n FROM pipeline.history WHERE topic='cdc.notifications' AND JSONExtractString(payload,'aggregate_type')='Initial Snapshot' AND JSONExtractString(payload,'type')='COMPLETED'"
            )[0]["n"]
        )
        > 0,
        180,
    )
    state["stage"] = "ready"
    save(ROOT / "results/fixture.json", state)


def wait_dag(name, run, path):
    def complete():
        state = metadata(
            f"SELECT state FROM dag_run WHERE dag_id='{name}' AND run_id='{run}'"
        )
        if state == "failed":
            raise AssertionError("Native Airflow DAG failed: " + name)
        return state == "success"

    wait(complete, 360)
    rows = json.loads(
        metadata(
            f"SELECT coalesce(json_agg(json_build_object('task_id',t.task_id,'state',t.state,'try_number',t.try_number)), '[]') FROM task_instance t WHERE t.dag_id='{name}' AND t.run_id='{run}'"
        )
    )
    save(path / (name + "-tasks.json"), rows)
    if not rows or any(row["state"] != "success" for row in rows):
        raise RuntimeError("DAG state lacks successful task evidence")


def remove(path):
    from correctness import settle, reconcile
    from observations import resources

    state = fixture()
    settle(path)
    reconcile(path)
    resources(path)
    job = json.loads((ROOT / "results/job.json").read_text())["id"]
    # The Flink CLI cancels processing only after all preserved source state is verified.
    command("exec", "-T", "jobmanager", "/opt/flink/bin/flink", "cancel", job)
    api("/connectors/margins", "DELETE")
    for host in ["ch1", "ch2", "ch3"]:
        sql("DROP DATABASE pipeline SYNC", host)
    mysql("DROP TABLE debezium_signal,receipts,orders,customers")
    save(
        ROOT / "results" / ("down-" + state["id"] + ".json"),
        {"verified": True, "evidence": str(path.relative_to(ROOT))},
    )


def destroy(path):
    state = fixture()
    if not json.loads(
        (ROOT / "results" / ("down-" + state["id"] + ".json")).read_text()
    )["verified"]:
        raise RuntimeError("No verified teardown receipt")
    built = {}
    for role in ["orders-workload", "jobmanager", "airflow-api"]:
        container = owned(role)
        tag = container["Config"]["Image"]
        if not tag.startswith("examples/mysql-margins-"):
            raise RuntimeError("Unexpected local build image; refusing cleanup")
        built[tag] = container["Image"]
    save(path / "owned-build-images.json", built)
    command("down", "--volumes", "--remove-orphans", "--rmi", "local", timeout=180)
    # Compose --rmi local can preserve explicitly tagged build images. Remove
    # only these verified example tags, never their upstream bases or build cache.
    for tag, identifier in built.items():
        remaining = subprocess.check_output(
            ["docker", "ps", "--all", "--quiet", "--filter", "ancestor=" + identifier],
            text=True,
        )
        if remaining.strip():
            raise RuntimeError("A container still uses the owned build image: " + tag)
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
            text=True,
            capture_output=True,
        )
        if inspected.returncode == 0:
            if inspected.stdout.strip() != identifier:
                raise RuntimeError("Owned build tag changed before cleanup: " + tag)
            subprocess.run(
                ["docker", "image", "rm", tag], check=True, capture_output=True
            )

    for kind in ["container", "network", "volume"]:
        raw = subprocess.check_output(
            [
                "docker",
                kind,
                "ls",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=" + PROJECT,
            ],
            text=True,
        )
        if raw.strip():
            raise RuntimeError("Owned resources remain: " + kind)
    (ROOT / "results/fixture.json").rename(
        ROOT / "results" / ("retired-fixture-" + state["id"] + ".json")
    )
    save(
        path / "status.json",
        {"passed": True, "fixture": state["id"], "owned_resources_absent": True},
    )
