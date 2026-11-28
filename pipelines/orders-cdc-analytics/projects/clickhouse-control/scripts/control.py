"""Serial matched phases in the owned CDC lab; do not overlap database budgets."""

import json
from pathlib import Path
import subprocess
import sys
import time
import os
import uuid

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT.parents[1]
sys.path.insert(0, str(PARENT / "scripts"))
import lab
import topology
import archive
import offsets as offset_audit

COMPOSE = [*lab.COMPOSE, "--profile", "control"]
DORIS = [f"fe{i}" for i in range(1, 4)] + [f"be{i}" for i in range(1, 4)]
FLINK = ["jobmanager", "taskmanager"]
CH = [f"ch{i}" for i in range(1, 4)] + [f"keeper{i}" for i in range(1, 4)]


def compose(*args, timeout=900):
    result = subprocess.run(
        [*COMPOSE, *map(str, args)],
        cwd=PARENT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"Compose exited {result.returncode}: {result.stdout}")
    return result.stdout


def check():
    endpoint = json.loads(
        subprocess.check_output(["docker", "context", "inspect"], text=True)
    )[0]["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith("unix://"):
        raise RuntimeError("Only the local Unix Docker engine is allowed")
    services = json.loads(compose("--profile", "tools", "config", "--format", "json"))[
        "services"
    ]
    phases = {}
    for name, excluded in [
        ("setup", set(CH) | {"comparison"}),
        ("doris_ingest", set(CH) | {"sql"}),
        ("doris_serving", set(CH + FLINK) | {"sql"}),
        ("clickhouse", set(DORIS + FLINK) | {"sql"}),
    ]:
        roles = [v for k, v in services.items() if k not in excluded]
        phases[name] = {
            "cpu": sum(
                float(v.get("cpus", 0)) * v.get("deploy", {}).get("replicas", 1)
                for v in roles
            ),
            "memory_bytes": sum(
                int(v.get("mem_limit", 0)) * v.get("deploy", {}).get("replicas", 1)
                for v in roles
            ),
        }
    info = json.loads(
        subprocess.check_output(["docker", "info", "--format", "{{json .}}"], text=True)
    )
    if any(
        v["cpu"] > info["NCPU"] or v["memory_bytes"] + 2 * 1024**3 > info["MemTotal"]
        for v in phases.values()
    ):
        raise RuntimeError(
            "Serial topology exceeds Docker resources plus 2 GiB reserve"
        )
    return phases


def directory(mode):
    path = ROOT / "results" / (mode + "-" + str(time.time_ns()))
    path.mkdir(parents=True)
    lab.save(path / "status.json", {"passed": False, "mode": mode})
    lab.save(path / "fixture.json", lab.fixture())
    return path


def sql(role, query):
    if role not in CH[:3]:
        raise ValueError("owned ClickHouse role required")
    return compose(
        "exec",
        "-T",
        role,
        "clickhouse-client",
        "--user",
        "lab",
        "--password",
        "local-control-only",
        "--multiquery",
        "--query",
        query,
        timeout=180,
    )


def application(path, backend, host, mode, replica=None):
    extra = ["-e", "REPLICA_INDEX=" + str(replica)] if replica is not None else []
    output = compose(
        "exec",
        "-T",
        *extra,
        "-e",
        "BACKEND=" + backend,
        "-e",
        "BACKEND_HOST=" + host,
        "comparison",
        "/comparison",
        mode,
        timeout=900,
    )
    (
        path
        / (
            backend
            + "-"
            + host
            + "-"
            + mode
            + "-replica-"
            + str(replica)
            + "-"
            + str(time.time_ns())
            + ".log"
        )
    ).write_text(output)
    return [json.loads(line) for line in output.splitlines()]


def verify_ch(path):
    for role in CH[:3]:
        for table in (
            "cdc_history",
            "orders_state",
            "customers_state",
            "write_witness",
        ):
            sql(role, "SYSTEM SYNC REPLICA pipeline." + table)
        output = sql(
            role,
            "SELECT table,total_replicas,active_replicas,queue_size,is_readonly,is_session_expired FROM system.replicas WHERE database='pipeline' ORDER BY table FORMAT JSONEachRow",
        )
        (path / (role + "-replicas.jsonl")).write_text(output)
        replicas = [json.loads(line) for line in output.splitlines()]
        if len(replicas) != 4 or any(
            int(r["total_replicas"]) != 3
            or int(r["active_replicas"]) != 3
            or int(r["queue_size"]) != 0
            or r["is_readonly"]
            or r["is_session_expired"]
            for r in replicas
        ):
            raise RuntimeError("Three current writable native replicas required")
        application(path, "clickhouse", role, "verify")
    audit = sql(
        "ch1",
        """WITH JSONExtractString(payload,'op') AS op,
 if(op='d',JSONExtractRaw(payload,'before'),JSONExtractRaw(payload,'after')) AS body,
 JSONExtractUInt(body,'id') AS id,JSONExtractUInt(payload,'source','lsn') AS lsn
 SELECT countIf(op NOT IN ('r','c','u','d') OR id=0 OR lsn=0) AS invalid
 FROM pipeline.cdc_history FORMAT JSONEachRow""",
    )
    (path / "source-contract-audit.jsonl").write_text(audit)
    if any(int(r["invalid"]) for r in map(json.loads, audit.splitlines())):
        raise RuntimeError("Invalid source contract")
    ties = sql(
        "ch1",
        """WITH JSONExtractString(payload,'op') AS op,
 if(op='d',JSONExtractRaw(payload,'before'),JSONExtractRaw(payload,'after')) AS body,
 JSONExtractUInt(body,'id') AS id,JSONExtractUInt(payload,'source','lsn') AS lsn
 SELECT topic,id,lsn,uniqExact(tuple(body,op)) AS variants FROM pipeline.cdc_history
 GROUP BY topic,id,lsn HAVING variants>1 LIMIT 1 FORMAT JSONEachRow""",
    )
    (path / "source-version-ties.jsonl").write_text(ties)
    if ties.strip():
        raise RuntimeError("Conflicting source version tie")
    readback = sql(
        "ch1",
        "SELECT database,name,engine_full,sorting_key,partition_key,create_table_query FROM system.tables WHERE database='pipeline' ORDER BY name LIMIT 32 FORMAT JSONEachRow",
    )
    (path / "native-schema-readback.jsonl").write_text(readback)
    settings = sql(
        "ch1",
        "SELECT name,value FROM system.settings WHERE name IN ('insert_quorum','insert_quorum_timeout','max_threads','max_memory_usage','use_query_cache','max_rows_to_read','max_bytes_to_read','max_execution_time') ORDER BY name LIMIT 32 FORMAT JSONEachRow",
    )
    (path / "native-client-settings.jsonl").write_text(settings)
    compression = sql(
        "ch1",
        "SELECT table,sum(data_compressed_bytes) AS compressed_bytes,sum(data_uncompressed_bytes) AS uncompressed_bytes,sum(bytes_on_disk) AS disk_bytes FROM system.parts WHERE active AND database='pipeline' GROUP BY table ORDER BY table FORMAT JSONEachRow",
    )
    (path / "native-compression.jsonl").write_text(compression)


def start_retained_doris(path):
    identifiers = []
    for role in DORIS:
        ids = compose("ps", "--all", "--quiet", role).split()
        if len(ids) != 1:
            raise RuntimeError("Exactly one existing owned retained role required")
        lab.assert_owned(ids[0], role)
        identifiers.extend(ids)
    # Bootstrap-only depends_on healthy cannot order a stopped voting quorum.
    started = subprocess.run(
        ["docker", "start", *identifiers], check=True, capture_output=True, text=True
    )
    (path / "retained-start.log").write_text(started.stdout + started.stderr)

    def healthy():
        rows = [lab.assert_owned(identifier) for identifier in identifiers]
        return all(
            row["State"]["Running"]
            and row["State"].get("Health", {}).get("Status") == "healthy"
            for row in rows
        )

    lab.wait_for(healthy, timeout=300)
    deadline = time.monotonic() + 120
    while True:
        try:
            topology.verify(path)
            break
        except RuntimeError as error:
            (path / ("retained-bootstrap-" + str(time.time_ns()) + ".log")).write_text(
                str(error)
            )
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def capture_resources(path):
    output = subprocess.check_output(
        [sys.executable, str(PARENT / "scripts/resources.py")], cwd=PARENT, text=True
    )
    (path / "volume-inventory-path.txt").write_text(output)
    counters = {}
    for role in CH:
        for identifier in compose("ps", "--quiet", role).split():
            native = lab.assert_owned(identifier, role)
            if not native["State"]["Running"]:
                continue
            result = compose(
                "exec",
                "-T",
                role,
                "sh",
                "-c",
                "for f in cpu.stat memory.peak memory.current io.stat; do echo METRIC:$f; cat /sys/fs/cgroup/$f; done",
            )
            counters[role] = {
                "id": native["Id"],
                "started_at": native["State"]["StartedAt"],
                "image": native["Image"],
                "memory_limit": native["HostConfig"]["Memory"],
                "nano_cpus": native["HostConfig"]["NanoCpus"],
                "native": result,
            }
    lab.save(path / "native-control-cgroups.json", counters)


def fence_source(path):
    freeze = compose(
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "lab",
        "-d",
        "orders",
        "-v",
        "ON_ERROR_STOP=1",
        "-qAt",
        "-c",
        "REVOKE INSERT,UPDATE,DELETE ON orders,customers FROM app; SELECT json_build_object('orders',has_table_privilege('app','orders','INSERT,UPDATE,DELETE'),'customers',has_table_privilege('app','customers','INSERT,UPDATE,DELETE'))",
    )
    fence = json.loads(freeze.strip())
    lab.save(path / "source-write-fence.json", fence)
    if any(fence.values()):
        raise RuntimeError("Source write fence did not take effect")


def serving_failure(path):
    value = json.loads((ROOT / "results/state.json").read_text())
    if value["stage"] != "compared" or value["fixture"] != lab.fixture()["id"]:
        raise RuntimeError("Completed matched phase required")
    case = uuid.uuid4().hex
    lab.save(path / "witness-case.json", {"case": case, "fixture": lab.fixture()["id"]})
    ledger = PARENT / "results" / ("control-writes-" + case + ".jsonl")

    def events():
        return (
            [json.loads(line) for line in ledger.read_text().splitlines()]
            if ledger.exists()
            else []
        )

    def acks():
        return sum(r.get("event") == "ack" for r in events())

    def start(mode, role, log):
        stream = (path / log).open("x")
        child = subprocess.Popen(
            [
                *COMPOSE,
                "exec",
                "-T",
                "-e",
                "BACKEND=clickhouse",
                "-e",
                "BACKEND_HOST=" + role,
                "-e",
                "CASE_ID=" + case,
                "comparison",
                "/comparison",
                mode,
            ],
            cwd=PARENT,
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
        return child, stream

    writer, ws = start("write-witness", "ch1", "writer.log")
    reader, rs = start("live-read", "ch2", "live-query-checks.log")
    victim = compose("ps", "--quiet", "ch3").strip()
    original = lab.assert_owned(victim, "ch3")
    try:

        def initial_progress():
            count = acks()
            if writer.poll() is not None and count < 5:
                raise RuntimeError(
                    "Witness writer exited before baseline ACKs; inspect writer.log"
                )
            return count >= 5

        lab.wait_for(initial_progress)
        before = acks()
        subprocess.run(
            ["docker", "kill", "--signal", "SIGKILL", victim],
            check=True,
            capture_output=True,
        )
        dead = lab.assert_owned(victim, "ch3")
        lab.save(
            path / "fault.json",
            {
                "case": case,
                "id": victim,
                "before": original["State"],
                "after": dead["State"],
                "acks_before": before,
            },
        )
        if (
            dead["State"]["Running"]
            or dead["State"]["ExitCode"] != 137
            or dead["State"]["OOMKilled"]
        ):
            raise RuntimeError("Native crash not proved")
        time.sleep(35)
        degraded = sql(
            "ch1",
            "SELECT table,active_replicas FROM system.replicas WHERE database='pipeline' ORDER BY table LIMIT 8 FORMAT JSONEachRow",
        )
        (path / "degraded-replicas.jsonl").write_text(degraded)
        observed = [json.loads(line) for line in degraded.splitlines()]
        if len(observed) != 4 or any(int(r["active_replicas"]) != 2 for r in observed):
            raise RuntimeError("Expected native two-active-replica degraded shape")
        after_expiry = acks()
        lab.wait_for(lambda: acks() > after_expiry)
        lab.wait_for(lambda: acks() >= before + 10)
        lab.save(
            path / "outage-progress.json",
            {
                "before": before,
                "while_down": acks(),
                "after_native_expiry": after_expiry,
                "hold_s": 35,
            },
        )
    finally:
        subprocess.run(["docker", "start", victim], check=True, capture_output=True)
        try:
            if writer.wait(timeout=180) != 0:
                raise RuntimeError("Writer failed; reconcile external witness")
            if reader.wait(timeout=180) != 0:
                raise RuntimeError("Live successful query violated oracle or errored")
        finally:
            ws.close()
            rs.close()
    lab.wait_for(
        lambda: lab.assert_owned(victim, "ch3")["State"].get("Health", {}).get("Status")
        == "healthy"
    )
    verify_ch(path)
    for role in CH[:3]:
        output = compose(
            "exec",
            "-T",
            "-e",
            "BACKEND=clickhouse",
            "-e",
            "BACKEND_HOST=" + role,
            "-e",
            "CASE_ID=" + case,
            "comparison",
            "/comparison",
            "verify-witness",
        )
        (path / (role + "-witness.log")).write_text(output)
    lab.save(
        path / "witness-summary.json",
        {
            "case": case,
            "ack_batches": acks(),
            "rows_per_batch": 8,
            "scope": "new direct native quorum writes; separate from frozen CDC analytical input",
        },
    )


def main():
    mode = sys.argv[1]
    if mode == "check":
        print(json.dumps(check()))
        return
    path = directory(mode)
    lab.save(path / "budgets.json", check())
    state = ROOT / "results/state.json"
    if mode == "up":
        compose("up", "-d", "comparison")
        topology.query(
            "CREATE VIEW pipeline.current_comparison AS SELECT * FROM pipeline.checkpointed"
        )
        job = lab.fixture()["job"]
        lab.api("/jobs/" + job, method="PATCH")
        lab.wait_for(lambda: lab.api("/jobs/" + job)["state"] == "CANCELED")
        lab.save(state, {"fixture": lab.fixture()["id"], "stage": "source-ready"})
        lab.save(ROOT / "results/fixture.json", lab.fixture())
    elif mode == "source":
        value = json.loads(state.read_text())
        if value["fixture"] != lab.fixture()["id"] or value["stage"] != "source-ready":
            raise RuntimeError("Fresh source-ready fixture required")
        for operation in ("seed", "dimensions", "delete"):
            lab.workload(operation, path)
        fence_source(path)
        n = int(os.environ.get("ORDER_COUNT", "1000000"))
        customers = int(os.environ.get("CUSTOMER_COUNT", "10000"))

        def offsets(topic, expected, partitions):
            output = compose(
                "exec",
                "-T",
                "-e",
                "KAFKA_HEAP_OPTS=-Xms32m -Xmx64m",
                "controller1",
                "/opt/kafka/bin/kafka-get-offsets.sh",
                "--bootstrap-server",
                "broker1:9092",
                "--topic",
                topic,
                "--time",
                "latest",
            )
            rows = [
                line.split(":")
                for line in output.splitlines()
                if line.startswith(topic + ":")
            ]
            return (
                output
                if len(rows) == partitions and sum(int(r[2]) for r in rows) == expected
                else None
            )

        for topic, expected, partitions in [
            ("cdc.public.orders", n + 2000, 4),
            ("cdc.public.customers", customers + min(100, customers // 4), 1),
        ]:
            output = lab.wait_for(
                lambda: offsets(topic, expected, partitions), timeout=180
            )
            (path / (topic + "-source-fence.txt")).write_text(output)
        lab.save(
            path / "source-events-ready.json",
            {
                "utc_ns": time.time_ns(),
                "orders_events": n + 2000,
                "customer_events": customers + min(100, customers // 4),
            },
        )
        compose("stop", "comparison")
        case = uuid.uuid4().hex
        query = (PARENT / "sql/pipeline.sql").read_text()
        query = query.replace(
            "INSERT INTO asynchronous SELECT * FROM enriched_orders ON CONFLICT DO DEDUPLICATE;",
            "",
        )
        query = (
            query.replace("orders_checkpointed", "control_" + case)
            .replace("pipeline-orders", "control_orders_" + case)
            .replace("pipeline-customers", "control_customers_" + case)
        )
        if query.count("INSERT INTO") != 1:
            raise RuntimeError("Control must have exactly one logical sink")
        (path / "single-sink.sql").write_text(query)
        submitted = subprocess.run(
            [
                *lab.COMPOSE,
                "run",
                "--rm",
                "-T",
                "sql",
                "-Drest.address=jobmanager",
                "-Djobmanager.rpc.address=jobmanager",
                "-f",
                "/dev/stdin",
            ],
            cwd=PARENT,
            input=query,
            text=True,
            capture_output=True,
            timeout=240,
        )
        (path / "single-sink-submission.log").write_text(
            submitted.stdout + submitted.stderr
        )
        if submitted.returncode or "[ERROR]" in submitted.stdout + submitted.stderr:
            raise RuntimeError("Single-sink submission failed")
        jobs = lab.wait_for(
            lambda: [
                job
                for job in lab.api("/jobs/overview")["jobs"]
                if job["state"] == "RUNNING"
            ]
        )
        if len(jobs) != 1:
            raise RuntimeError("Exactly one single-sink control job required")
        lab.save(
            path / "single-sink-plan.json", lab.api("/jobs/" + jobs[0]["jid"] + "/plan")
        )
        fixture = lab.fixture()
        fixture["planned_cancelled_job"] = fixture["job"]
        fixture["job"] = jobs[0]["jid"]
        lab.save(PARENT / "results/fixture.json", fixture)
        lab.save(ROOT / "results/fixture.json", fixture)
        compose("start", "comparison")
        deadline = time.monotonic() + 300
        while True:
            try:
                application(path, "doris", "fe1", "verify")
                break
            except RuntimeError as error:
                (path / ("catchup-" + str(time.time_ns()) + ".log")).write_text(
                    str(error)
                )
                if time.monotonic() >= deadline:
                    raise
                time.sleep(2)
        lab.workload("verify-receipts", path)
        lab.native(path)
        value["stage"] = "doris"
        lab.save(state, value)
    elif mode in ("compare", "resume-compare"):
        value = json.loads(state.read_text())
        if value["fixture"] != lab.fixture()["id"]:
            raise RuntimeError("Foreign fixture phase")
        if mode == "compare" and value["stage"] != "doris":
            raise RuntimeError("Unexpected phase; use explicit resume")
        if mode == "resume-compare" and value["stage"] == "doris":
            if compose("ps", "--status", "running", "--quiet", *FLINK).strip():
                raise RuntimeError("Legacy serving resume requires fenced Flink roles")
            # Original benchmark failed after stopping Flink, before its next marker.
            lab.save(
                path / "resume-after-parser-error.json",
                {
                    "from_stage": "doris",
                    "reason": "parser failure after owned Flink fencing; source/data preserved",
                },
            )
            value["stage"] = "doris-serving"
        if value["stage"] == "doris":
            lab.workload("verify-receipts", path)
            lab.native(path)
            for replica in range(3):
                application(path, "doris", "fe1", "verify", replica)
            fence_source(path)
            job = lab.fixture()["job"]
            lab.api("/jobs/" + job, method="PATCH")
            lab.wait_for(lambda: lab.api("/jobs/" + job)["state"] == "CANCELED")
            lab.save(path / "cancelled-job.json", lab.api("/jobs/" + job))
            for role in FLINK:
                for identifier in compose("ps", "--quiet", role).split():
                    lab.assert_owned(identifier, role)
            compose("stop", *FLINK)
            value["stage"] = "doris-serving"
            lab.save(state, value)
        if value["stage"] == "doris-serving":
            if compose("ps", "--status", "running", "--quiet", *FLINK).strip():
                raise RuntimeError(
                    "Flink must be outside both serving measurement phases"
                )
            fence_source(path)
            topology.verify(path)
            lab.workload("verify-receipts", path)
            reference = application(path, "doris", "fe1", "verify")
            value["source_fingerprint"] = next(
                r["source_sha256"] for r in reference if r.get("event") == "verified"
            )
            lab.save(state, value)
            lab.save(path / "doris-serving-start.json", {"utc_ns": time.time_ns()})
            application(path, "doris", "fe1", "benchmark")
            lab.save(path / "doris-serving-end.json", {"utc_ns": time.time_ns()})
            value["stage"] = "fenced"
            lab.save(state, value)
        if value["stage"] == "fenced":
            for role in DORIS + FLINK:
                for identifier in compose("ps", "--quiet", role).split():
                    lab.assert_owned(identifier, role)
            compose("stop", *FLINK, *DORIS)
            if compose("ps", "--status", "running", "--quiet", *FLINK, *DORIS).strip():
                raise RuntimeError("Doris/Flink still running")
            compose("up", "-d", "--wait", *CH, timeout=600)
            for role in CH[:3]:
                sql(role, (ROOT / "sql/state.sql").read_text())
            lab.save(path / "clickhouse-capture-start.json", {"utc_ns": time.time_ns()})
            for role in CH[:3]:
                sql(role, (ROOT / "sql/ingest.sql").read_text())
            value["stage"] = "clickhouse"
            lab.save(state, value)
        if value["stage"] == "clickhouse":
            deadline = time.monotonic() + 300
            while True:
                try:
                    application(path, "clickhouse", "ch1", "verify")
                    break
                except RuntimeError as error:
                    (path / ("catchup-" + str(time.time_ns()) + ".log")).write_text(
                        str(error)
                    )
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(2)
            verify_ch(path)
            lab.save(path / "clickhouse-serving-start.json", {"utc_ns": time.time_ns()})
            application(path, "clickhouse", "ch1", "benchmark")
            lab.save(path / "clickhouse-serving-end.json", {"utc_ns": time.time_ns()})
            value["stage"] = "compared"
            lab.save(state, value)
        if value["stage"] != "compared":
            raise RuntimeError("Comparison did not reach a qualified phase")
    elif mode == "serving-failure":
        serving_failure(path)
    elif mode == "resources":
        capture_resources(path)
    elif mode == "verify":
        verify_ch(path)
    elif mode == "down":
        value = json.loads(state.read_text())
        if value["fixture"] != lab.fixture()["id"]:
            raise RuntimeError("Foreign fixture state")
        verify_ch(path)
        for case_file in (ROOT / "results").glob("serving-failure-*/witness-case.json"):
            witness = json.loads(case_file.read_text())
            if witness["fixture"] != lab.fixture()["id"]:
                continue
            for role in CH[:3]:
                output = compose(
                    "exec",
                    "-T",
                    "-e",
                    "BACKEND=clickhouse",
                    "-e",
                    "BACKEND_HOST=" + role,
                    "-e",
                    "CASE_ID=" + witness["case"],
                    "comparison",
                    "/comparison",
                    "verify-witness",
                )
                (path / (role + "-witness-" + witness["case"] + ".log")).write_text(
                    output
                )
        final = application(path, "clickhouse", "ch1", "verify")
        if (
            next(r["source_sha256"] for r in final if r.get("event") == "verified")
            != value["source_fingerprint"]
        ):
            raise RuntimeError("Source changed across comparison")
        offset_audit.audit(ROOT, compose, path, lab.fixture()["id"])
        archive.preserve(ROOT, PARENT, COMPOSE, path, lab.fixture()["id"])
        compose("stop", *CH)
        start_retained_doris(path)
        for replica in range(3):
            application(path, "doris", "fe1", "verify", replica)
        lab.workload("verify-receipts", path)
        topology.verify(path)
        # Same final reader binary as ClickHouse; the earlier reader's results remain diagnostics.
        lab.save(path / "doris-serving-start.json", {"utc_ns": time.time_ns()})
        application(path, "doris", "fe1", "benchmark")
        lab.save(path / "doris-serving-end.json", {"utc_ns": time.time_ns()})
        capture_resources(path)
        lab.api("/connectors/orders", connect=True, method="DELETE")
        lab.workload("drop", path)
        lab.save(
            PARENT / "results" / ("down-" + lab.fixture()["id"] + ".json"),
            {
                "fixture": lab.fixture()["id"],
                "verified": True,
                "evidence": str(path.relative_to(PARENT)),
            },
        )
    else:
        raise ValueError("unknown mode")
    if mode in ("source", "compare", "resume-compare", "serving-failure"):
        capture_resources(path)
    lab.save(
        path / "status.json",
        {"passed": True, "mode": mode, "fixture": lab.fixture()["id"]},
    )
    print(path)


if __name__ == "__main__":
    main()
