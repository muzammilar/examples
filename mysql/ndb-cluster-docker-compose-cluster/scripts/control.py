"""One example's lifecycle, native faults and historical correctness checks."""

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from report import observations, timestamp

PROJECT = "mysql-ndb-cluster"
ROOT = Path("results")
COMPOSE = ["docker", "compose", "--project-name", PROJECT]


def now():
    return datetime.now(timezone.utc).isoformat()


def run(args, *, capture=True, timeout=180, check=True, env=None):
    result = subprocess.run(
        args, text=True, capture_output=capture, timeout=timeout, env=env
    )
    if check and result.returncode:
        raise RuntimeError(
            f"{args}: {result.stderr[-4000:] if capture else result.returncode}"
        )
    return result.stdout if check else result


def compose(*args, **options):
    return run([*COMPOSE, *args], **options)


def exclusive_save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def phase(case, name, markers):
    temporary = case / "phase.tmp"
    temporary.write_text(name + "\n")
    temporary.replace(case / "phase")
    markers.append({"phase": name, "at": now()})


def owned(service):
    ids = compose("ps", "-aq", service).split()
    if len(ids) != 1:
        raise RuntimeError("expected exactly one owned container: " + service)
    info = json.loads(run(["docker", "inspect", ids[0]]))[0]
    labels = info["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != service
    ):
        raise RuntimeError("refusing foreign container: " + service)
    return info


def check_directory(folder, name):
    result = folder / "checks" / (name + "-" + str(time.time_ns()))
    result.mkdir(parents=True)
    result.chmod(0o777)
    return result


def read_events(path):
    return (
        [json.loads(line) for line in path.read_text().split("\n")[:-1]]
        if path.exists()
        else []
    )


def progress(case, writer, reader, after, *, writes=True, shard=None, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if reader.poll() is not None or (writes and writer.poll() is not None):
            raise RuntimeError("live writer/reader exited before native recovery")
        reads = sum(
            e["outcome"] == "ok"
            and timestamp(e["at"]) > timestamp(after)
            and (shard is None or e["shard"] == shard)
            for e in read_events(case / "reads.jsonl")
        )
        acks = sum(
            e["stage"] == "ack"
            and timestamp(e["at"]) > timestamp(after)
            and (shard is None or e["shard"] == shard)
            for e in read_events(case / "writes.jsonl")
        )
        if reads >= 3 and (not writes or acks >= 3):
            return
        time.sleep(0.5)
    raise RuntimeError("no independent read/write progress before deadline")


def scenario(mode):
    folder, config = fixture()
    verify(folder)
    run_id = os.environ.get("RUN_ID") or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("RUN_ID must be a simple unique name")
    case = folder / "runs" / run_id
    case.mkdir(parents=True)
    case.chmod(0o777)
    count, batch = (
        int(os.environ.get("COUNT", "1000000")),
        int(os.environ.get("BATCH", "100")),
    )
    live = mode not in ("test", "benchmark")
    reserved = int(os.environ.get("MAX_ROWS", str(count * 10))) if live else count
    if not (1 <= count <= reserved <= 100000000 and 1 <= batch <= 10000):
        raise ValueError(
            "require 1 <= COUNT <= MAX_ROWS <= 100000000 and BATCH=1..10000"
        )
    previous = [json.loads(p.read_text()) for p in (folder / "runs").glob("*/run.json")]
    base = max((p["base"] + p["reserved"] for p in previous), default=0)
    exclusive_save(
        case / "run.json",
        {
            "base": base,
            "reserved": reserved,
            "minimum": count,
            "batch": batch,
            "customers": config["customers"],
            "live": live,
            "mode": mode,
        },
    )
    env = dict(
        os.environ,
        WITNESS_DIR=str(folder.resolve()),
        RESULTS_DIR=str(case.resolve()),
        RATE=os.environ.get("CHAOS_RATE", "25000") if live else "0",
    )
    markers = []
    phase(case, "before", markers)
    token = uuid.uuid4().hex
    names = {
        role: PROJECT + "-" + role + "-" + token[:12] for role in ("writer", "reader")
    }
    processes, logs = {}, []
    target = None
    proof = {}
    started = time.monotonic()
    try:
        for role, action in (("reader", "watch"), ("writer", "load")):
            if role == "writer":
                progress(
                    case, None, processes["reader"], markers[-1]["at"], writes=False
                )
            log = (case / (role + ".log")).open("w")
            logs.append(log)
            processes[role] = subprocess.Popen(
                [
                    *COMPOSE,
                    "run",
                    "--rm",
                    "-T",
                    "--name",
                    names[role],
                    "--label",
                    "lab.case=" + token,
                    "workload",
                    action,
                ],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        writer, reader = processes["writer"], processes["reader"]
        if live:
            progress(case, writer, reader, markers[-1]["at"])
            before = snapshot()
            shard = int(os.environ.get("FAULT_PARTITION", "0"))
            if shard not in range(3):
                raise ValueError("FAULT_PARTITION must be 0..2")
            proof["affected_partition"] = shard
            proof["survivors_before"] = before
            progress(case, writer, reader, markers[-1]["at"], shard=shard)
            if mode == "data":
                part = next(
                    p
                    for p in fragments(config["tables"]["orders"])
                    if int(p["partition_id"]) == shard
                )
                validate_fragment(part)
                target = "data" + part["current_primary"]
                proof["fragment_before"] = part
            elif mode == "sql":
                target = os.environ.get("FAULT_NODE", "sql0")
                if target not in SQL:
                    raise ValueError("FAULT_NODE must be sql0 or sql1")
                sessions = sql(
                    target,
                    "SELECT COUNT(*) FROM information_schema.processlist WHERE USER='app'",
                )
                if int(sessions) < 1:
                    raise RuntimeError(
                        "target SQL frontend had no live application sessions"
                    )
                proof["target_application_sessions"] = int(sessions)
            elif mode == "management":
                target = os.environ.get("FAULT_NODE", "mgm0")
                if target not in ("mgm0", "mgm1"):
                    raise ValueError("FAULT_NODE must be mgm0 or mgm1")
            elif mode == "router":
                target = "router"
            else:
                raise ValueError(mode)
            identity = owned(target)
            phase(case, "outage", markers)
            began = time.monotonic()
            compose("kill", "-s", "SIGKILL", target)
            killed = owned(target)
            if killed["State"]["Running"] or killed["State"]["ExitCode"] != 137:
                raise RuntimeError("container SIGKILL not proven")
            proof["killed_member"] = {
                "node": target,
                "id": identity["Id"],
                "exit": killed["State"],
            }
            if mode == "data":
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    try:
                        parts = fragments(config["tables"]["orders"])
                    except RuntimeError as error:
                        proof.setdefault("native_lookup_errors", []).append(
                            {"at": now(), "error": str(error)}
                        )
                        time.sleep(0.5)
                        continue
                    current = next(p for p in parts if int(p["partition_id"]) == shard)
                    new = "data" + current["current_primary"]
                    if new != target and int(current["num_alive_replicas"]) == 2:
                        proof["degraded_fragment"] = current
                        validate_fragment(current, healthy=False)
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError("native fragment did not fail over")
                proof["promotion_seconds"] = time.monotonic() - began
                proof["new_primary"] = new
                proof["degraded_fragment"] = current
            if mode != "router":
                phase(case, "degraded", markers)
                progress(case, writer, reader, markers[-1]["at"], shard=shard)
            time.sleep(10)
            phase(case, "recovery", markers)
            compose(
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "300",
                target,
                timeout=360,
            )
            repair_started = time.monotonic()
            proof["native_repair_under_load"] = wait_native(folder)
            proof["native_repair_barrier_seconds"] = time.monotonic() - repair_started
            proof["native_repair_completed_at"] = now()
            progress(case, writer, reader, markers[-1]["at"], shard=shard)
            after = snapshot()
            proof["survivors_after"] = after
            for service, identity in before.items():
                if service != target and after.get(service) != identity:
                    raise RuntimeError(
                        "unrelated survivor process/container changed: " + service
                    )
            proof["unchanged_survivors"] = [s for s in before if s != target]
            target = None
            phase(case, "after", markers)
            progress(case, writer, reader, markers[-1]["at"])
            (case / "stop-writer").touch()
        else:
            phase(case, "load", markers)
        if writer.wait(timeout=1200) != 0:
            raise RuntimeError("writer failed; preserve its journal and log")
        phase(case, "verification", markers)
        proof["full_historical_reconciliation"] = verify(folder)
        phase(case, "after", markers)
        progress(case, writer, reader, markers[-1]["at"], writes=False)
        (case / "stop-reader").touch()
        if reader.wait(timeout=30) != 0:
            raise RuntimeError("independent reader failed")
        report = observations(case, markers, now())
        proof["observations"] = report
        if report["contract_failures"]:
            raise RuntimeError("known immutable read returned missing/wrong data")
        exclusive_save(
            case / "result.json",
            {
                "mode": mode,
                "seconds": time.monotonic() - started,
                "markers": markers,
                "load": json.loads((case / "load.json").read_text()),
                "proof": proof,
            },
        )
        print("Passed", mode, "evidence:", case, flush=True)
    except BaseException as error:
        try:
            proof["observations_at_failure"] = observations(case, markers, now())
        except (OSError, ValueError, KeyError) as observation_error:
            proof["observation_error"] = str(observation_error)
        exclusive_save(
            case / "failure.json",
            {"at": now(), "error": str(error), "markers": markers, "proof": proof},
        )
        raise
    finally:
        cleanup_errors = []
        if not (case / "result.json").exists():
            (case / "abort-writer").touch()
        if target:
            try:
                compose(
                    "up",
                    "-d",
                    "--no-deps",
                    "--wait",
                    "--wait-timeout",
                    "300",
                    target,
                    timeout=360,
                )
            except Exception as error:
                cleanup_errors.append(str(error))
        for role in ("writer", "reader"):
            (case / ("stop-" + role)).touch()
        for role, process in processes.items():
            if process.poll() is None:
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    info = run(["docker", "inspect", names[role]], check=False)
                    if (
                        info.returncode == 0
                        and json.loads(info.stdout)[0]["Config"]["Labels"].get(
                            "lab.case"
                        )
                        == token
                    ):
                        run(["docker", "stop", "--timeout", "5", names[role]])
                    process.wait(timeout=20)
        for log in logs:
            log.close()
        if cleanup_errors:
            exclusive_save(case / "cleanup-failure.json", cleanup_errors)
            if sys.exc_info()[0] is None:
                raise RuntimeError(str(cleanup_errors))


DATA = tuple(f"data{i}" for i in range(1, 10))
SQL = ("sql0", "sql1")
SERVICES = ("mgm0", "mgm1", *DATA, *SQL, "router")
TABLES = ("customers", "orders", "subscribers", "lab_identity")


def sql(node, statement, *, headers=False, timeout=60):
    if node not in SQL:
        raise ValueError("not an owned SQL frontend")
    args = [
        *COMPOSE,
        "exec",
        "-T",
        "-e",
        "MYSQL_PWD=local-root",
        node,
        "mysql",
        "-uroot",
        "-Dorders",
        "--batch",
    ]
    if not headers:
        args.append("--skip-column-names")
    result = subprocess.run(
        args, input=statement, text=True, capture_output=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError("native SQL failed: " + result.stderr[-4000:])
    return result.stdout.rstrip("\r\n")


def rows(node, statement):
    lines = sql(node, statement, headers=True).splitlines()
    if not lines:
        # mysql omits headers for a successful empty result (notably restart_info
        # before the first restart). Required inventories check their cardinality.
        return []
    fields = lines[0].split("\t")
    return [dict(zip(fields, line.split("\t"), strict=True)) for line in lines[1:]]


def volume_identity(service):
    info = owned(service)
    mounts = [
        m
        for m in info["Mounts"]
        if m["Destination"] == "/var/lib/mysql" and m["Type"] == "volume"
    ]
    if len(mounts) != 1 or mounts[0]["Name"] != PROJECT + "_" + service:
        raise RuntimeError("refusing unknown NDB storage: " + service)
    volume = json.loads(run(["docker", "volume", "inspect", mounts[0]["Name"]]))[0]
    if volume["Labels"].get("com.docker.compose.project") != PROJECT:
        raise RuntimeError("refusing foreign NDB volume")
    return {"name": volume["Name"], "created": volume["CreatedAt"]}


def identities():
    result = {}
    for node in SQL:
        exists = sql(
            node,
            "SELECT COUNT(*) FROM information_schema.tables WHERE TABLE_SCHEMA='orders' AND TABLE_NAME='lab_identity'",
        )
        if exists == "1":
            result[node] = json.loads(
                sql(
                    node,
                    "SELECT JSON_OBJECT('id',fixture_id,'customers',customers,'subscribers',subscribers) FROM lab_identity WHERE slot=1",
                )
            )
    return result


def fixture():
    native = identities()
    if set(native) != set(SQL) or native[SQL[0]] != native[SQL[1]]:
        raise RuntimeError("native SQL frontends disagree on fixture identity")
    identity = native[SQL[0]]
    folder = ROOT / str(uuid.UUID(identity["id"]))
    config = json.loads((folder / "fixture.json").read_text())
    if identity != {k: config[k] for k in ("id", "customers", "subscribers")}:
        raise RuntimeError("native identity differs from witness")
    if set(config["physical"]) != set(SQL) or set(config["volumes"]) != set(DATA):
        raise RuntimeError("incomplete physical NDB witness")
    for node, expected in config["physical"].items():
        if sql(node, "SELECT @@GLOBAL.server_uuid") != expected:
            raise RuntimeError("SQL frontend identity changed: " + node)
    for node, expected in config["volumes"].items():
        if volume_identity(node) != expected:
            raise RuntimeError("NDB data volume identity changed: " + node)
    if not (folder / "seeded.json").exists():
        raise RuntimeError("resume make up to verify seed")
    return folder, config


def dictionary():
    return rows(
        "sql0",
        "SELECT table_id,table_name,logging,fully_replicated,fragment_type,fragments,partitions FROM ndbinfo.dictionary_tables WHERE database_name='orders'",
    )


def fragments(table_id):
    return rows(
        "sql0", f"SELECT * FROM ndbinfo.table_fragments WHERE table_id={int(table_id)}"
    )


def validate_fragment(row, *, healthy=True):
    partition = int(row["partition_id"])
    if (
        partition not in range(3)
        or int(row["fragment_id"]) != partition
        or int(row["no_of_replicas"]) not in ((3,) if healthy else (2, 3))
        or int(row["current_third_backup"]) != 0
    ):
        raise RuntimeError("unexpected native fragment configuration")
    expected = set(range(partition * 3 + 1, partition * 3 + 4))
    current = {
        int(row[k])
        for k in ("current_primary", "current_first_backup", "current_second_backup")
    } - {0}
    if not current <= expected or int(row["current_primary"]) not in expected:
        raise RuntimeError("native fragment placed in wrong node group")
    if healthy and (
        current != expected
        or int(row["num_alive_replicas"]) != 3
        or int(row["num_dead_replicas"]) != 0
    ):
        raise RuntimeError("native fragment redundancy not repaired")
    return partition, expected


def wait_native(folder, deadline_seconds=300):
    config = json.loads((folder / "fixture.json").read_text())
    deadline = time.monotonic() + deadline_seconds
    last = None
    while time.monotonic() < deadline:
        try:
            for node in SQL:
                if (
                    sql(
                        node, "SHOW STATUS LIKE 'Ndb_number_of_ready_data_nodes'"
                    ).split("\t")[-1]
                    != "9"
                ):
                    raise RuntimeError(node + " does not see all nine data nodes")
                if sql(node, "SELECT @@GLOBAL.server_uuid") != config["physical"][node]:
                    raise RuntimeError("physical SQL UUID changed")
            membership = rows(
                "sql0",
                "SELECT node_id,status,uptime,config_generation FROM ndbinfo.nodes",
            )
            if {int(n["node_id"]) for n in membership} != set(range(1, 10)) or any(
                n["status"] != "STARTED" for n in membership
            ):
                raise RuntimeError("native data-node membership not STARTED")
            processes = rows(
                "sql0",
                "SELECT node_id,node_type,process_id,angel_process_id FROM ndbinfo.processes",
            )
            if {int(n["node_id"]) for n in processes} != {
                *range(1, 10),
                49,
                50,
                51,
                52,
            }:
                raise RuntimeError(
                    "native data/SQL/management process membership incomplete"
                )
            metadata = {r["table_name"]: r for r in dictionary()}
            if not set(TABLES) <= set(metadata):
                raise RuntimeError("native table metadata missing")
            placements = {}
            for table in TABLES:
                meta = metadata[table]
                if (
                    int(meta["table_id"]) != config["tables"][table]
                    or meta["logging"] != "1"
                    or meta["fully_replicated"] != "0"
                    or meta["partitions"] != "3"
                    or meta["fragments"] != "3"
                    or meta["fragment_type"] != "HashMapPartition"
                ):
                    raise RuntimeError(
                        "native table definition/object identity changed: " + table
                    )
                parts = fragments(meta["table_id"])
                if len(parts) != 3 or {int(p["partition_id"]) for p in parts} != {
                    0,
                    1,
                    2,
                }:
                    raise RuntimeError("native partition inventory differs")
                replicas = rows(
                    "sql0",
                    f"SELECT fragment_id,replica_node_id,is_replica_alive,last_max_gci_completed,last_lcp_id FROM ndbinfo.table_replicas WHERE table_id={int(meta['table_id'])} AND is_replica_alive=1",
                )
                if len(replicas) != 9:
                    raise RuntimeError("native live fragment-replica inventory differs")
                for part in parts:
                    index, expected = validate_fragment(part)
                    actual = [
                        int(r["replica_node_id"])
                        for r in replicas
                        if int(r["fragment_id"]) == index
                    ]
                    if len(actual) != 3 or set(actual) != expected:
                        raise RuntimeError("native live replicas have wrong placement")
                placements[table] = {"fragments": parts, "replicas": replicas}
            memory = rows(
                "sql0",
                "SELECT node_id,used,total FROM ndbinfo.memoryusage WHERE memory_type='Data memory'",
            )
            if len(memory) != 9 or any(
                int(m["total"]) != 512 * 1024 * 1024 for m in memory
            ):
                raise RuntimeError("native data-memory allocation differs")
            if any(int(m["used"]) > int(m["total"]) * 0.90 for m in memory):
                raise ValueError(
                    "NDB data memory exceeds the 90% lab guard; preserve history and increase capacity"
                )
            disk = compose(
                "exec", "-T", "sql0", "df", "-P", "-B1", "/var/lib/mysql"
            ).splitlines()
            if len(disk) != 2 or int(disk[1].split()[3]) < 6 * 1024**3:
                raise ValueError(
                    "Docker VM has less than 6 GiB free; stop this lab before shared disk exhaustion"
                )
            marker = identities()
            if set(marker) != set(SQL) or any(
                v != {k: config[k] for k in ("id", "customers", "subscribers")}
                for v in marker.values()
            ):
                raise RuntimeError("native dataset marker differs")
            return {
                "membership": membership,
                "processes": processes,
                "placements": placements,
                "data_memory": memory,
                "restart_info": rows("sql0", "SELECT * FROM ndbinfo.restart_info"),
            }
        except RuntimeError as error:
            last = str(error)
            time.sleep(1)
    raise RuntimeError("native NDB repair did not finish: " + str(last))


def tool(folder, case, action, *, node=None, capture=False):
    args = ["run", "--rm", "-T"]
    if node:
        if node not in SQL:
            raise ValueError("not an owned SQL frontend")
        owned(node)
        args += [
            "-e",
            f"DSN=app:local-app@tcp({node}:3306)/orders?interpolateParams=true&timeout=2s&readTimeout=15s&writeTimeout=15s",
        ]
    env = dict(
        os.environ, WITNESS_DIR=str(folder.resolve()), RESULTS_DIR=str(case.resolve())
    )
    return compose(*args, "workload", action, capture=capture, timeout=900, env=env)


def native_export(case, table):
    if table not in ("customers", "orders"):
        raise ValueError("not a supported native scan table")
    token = uuid.uuid4().hex
    name = PROJECT + "-scan-" + token[:12]
    args = [
        *COMPOSE,
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "--name",
        name,
        "--label",
        "lab.scan=" + token,
        "fragment-scan",
        "--ndb-connectstring=mgm0:1186,mgm1:1186",
        "--ndb-nodeid=53",
        "--connect-retries=3",
        "--connect-retry-delay=1",
        "--database=orders",
        "--rowid",
        "--parallelism=3",
        table,
    ]
    with (
        (case / "native-scan.gz").open("xb") as output,
        (case / "native-scan.log").open("w") as log,
    ):
        compressor = subprocess.Popen(
            ["gzip", "-c"], stdin=subprocess.PIPE, stdout=output
        )
        scan = subprocess.Popen(args, stdout=compressor.stdin, stderr=log)
        compressor.stdin.close()
        try:
            code = scan.wait(timeout=900)
            compressed = compressor.wait(timeout=60)
            if code or compressed:
                raise RuntimeError(
                    f"native scan/gzip failed: {code}/{compressed}; preserve raw evidence"
                )
        finally:
            if scan.poll() is None:
                # Stop only this token-labelled utility container, not a data node.
                info = run(["docker", "inspect", name], check=False)
                if info.returncode == 0:
                    value = json.loads(info.stdout)[0]
                    if (
                        value["Config"]["Labels"].get("lab.scan") != token
                        or value["Config"]["Labels"].get("com.docker.compose.project")
                        != PROJECT
                    ):
                        raise RuntimeError("refusing foreign native scan container")
                    run(["docker", "stop", "--timeout", "5", name])
                scan.wait(timeout=30)
            if compressor.poll() is None:
                compressor.terminate()
                compressor.wait(timeout=10)


def verify(folder):
    native = wait_native(folder)
    reports = {}
    for node in (None, *SQL):
        case = check_directory(folder, node or "router")
        tool(folder, case, "verify", node=node)
        reports[node or "router"] = json.loads((case / "check.json").read_text())
    for table in ("customers", "orders"):
        case = check_directory(folder, "native-" + table)
        native_export(case, table)
        tool(folder, case, "native-" + table)
        reports["native-" + table] = json.loads((case / "check.json").read_text())
    result = {"at": now(), "native": native, "logical_readbacks": reports}
    exclusive_save(
        folder / "checks" / ("full-" + str(time.time_ns()) + ".json"), result
    )
    print(
        "Verified full SQL bodies and native row/fragment placement; nine-copy membership/repair:",
        reports["router"],
        flush=True,
    )
    return result


def process_identity(service, pid):
    stat = compose("exec", "-T", service, "cat", f"/proc/{int(pid)}/stat")
    return {"pid": int(pid), "started": stat.rsplit(")", 1)[1].split()[19]}


def snapshot():
    processes = rows("sql0", "SELECT node_id,process_id FROM ndbinfo.processes")
    pids = {int(p["node_id"]): int(p["process_id"]) for p in processes}
    result = {}
    for service in SERVICES:
        info = owned(service)
        result[service] = {
            "id": info["Id"],
            "started": info["State"]["StartedAt"],
            "restarts": info["RestartCount"],
            "running": info["State"]["Running"],
            "paused": info["State"]["Paused"],
        }
        if service in DATA:
            result[service]["ndbmtd"] = process_identity(
                service, pids[int(service[4:])]
            )
        elif service in SQL:
            result[service]["mysqld"] = process_identity(
                service, pids[51 + SQL.index(service)]
            )
        else:
            result[service]["main"] = process_identity(service, 1)
    return result


def setup():
    customers, subscribers = (
        int(os.environ.get("CUSTOMERS", "10000")),
        int(os.environ.get("SUBSCRIBERS", "1000000")),
    )
    if not 1 <= customers <= 1000000 or not 1 <= subscribers <= 10000000:
        raise ValueError("invalid customer/subscriber fixture size")
    members = identities()
    if members:
        if set(members) != set(SQL) or members[SQL[0]] != members[SQL[1]]:
            raise RuntimeError("SQL frontends disagree on retained identity")
        native = members[SQL[0]]
        if native["customers"] != customers or native["subscribers"] != subscribers:
            raise RuntimeError("retained fixture sizes differ")
        folder = ROOT / str(uuid.UUID(native["id"]))
        config = json.loads((folder / "fixture.json").read_text())
        if set(config["physical"]) != set(SQL) or set(config["volumes"]) != set(DATA):
            raise RuntimeError("incomplete physical NDB witness")
        for node, expected in config["physical"].items():
            if sql(node, "SELECT @@GLOBAL.server_uuid") != expected:
                raise RuntimeError("retained SQL UUID changed")
        for node, expected in config["volumes"].items():
            if volume_identity(node) != expected:
                raise RuntimeError("retained NDB volume changed")
    else:
        if (
            sql(
                "sql0",
                "SELECT COUNT(*) FROM information_schema.tables WHERE TABLE_SCHEMA='orders' AND TABLE_NAME IN('customers','orders','subscribers')",
            )
            != "0"
        ):
            raise RuntimeError("legacy tables lack witness; refusing reset/adoption")
        identity = str(uuid.uuid4())
        folder = ROOT / identity
        config = {
            "id": identity,
            "customers": customers,
            "subscribers": subscribers,
            "physical": {
                node: sql(node, "SELECT @@GLOBAL.server_uuid") for node in SQL
            },
            "volumes": {node: volume_identity(node) for node in DATA},
            "created": now(),
        }
        exclusive_save(folder / "fixture.json", config)
        sql(
            "sql0",
            "CREATE TABLE lab_identity(slot INT PRIMARY KEY,fixture_id CHAR(36) NOT NULL,customers INT NOT NULL,subscribers INT NOT NULL) ENGINE=NDBCLUSTER PARTITION BY KEY(slot) PARTITIONS 3;"
            + f"INSERT INTO lab_identity VALUES(1,'{identity}',{customers},{subscribers})",
        )
    sql("sql0", Path("sql/schema.sql").read_text(), timeout=180)
    table_ids = {
        r["table_name"]: int(r["table_id"])
        for r in dictionary()
        if r["table_name"] in TABLES
    }
    if set(table_ids) != set(TABLES):
        raise RuntimeError("native schema incomplete")
    if "tables" not in config:
        config["tables"] = table_ids
        exclusive_save(folder / "fixture-configured.json", config)
        (folder / "fixture-configured.json").replace(folder / "fixture.json")
    elif table_ids != config["tables"]:
        raise RuntimeError("native table identities changed")
    if not (folder / "routing.json").exists():
        routing = []
        raw = []
        for start in range(0, customers, 1000):
            text = sql(
                "sql0",
                "\n".join(
                    f"EXPLAIN SELECT op_id FROM orders WHERE customer_id={c};"
                    for c in range(start, min(start + 1000, customers))
                ),
                timeout=180,
            )
            lines = text.splitlines()
            if len(lines) != min(1000, customers - start):
                raise RuntimeError("native EXPLAIN routing oracle incomplete")
            for line in lines:
                fields = line.split("\t")
                if fields[:3] != ["1", "SIMPLE", "orders"] or fields[3] not in (
                    "p0",
                    "p1",
                    "p2",
                ):
                    raise RuntimeError(
                        "native partition pruning did not identify one partition"
                    )
                routing.append(int(fields[3][1:]))
            raw.append(text)
        exclusive_save(folder / "routing.json", routing)
        (folder / "routing-explain.tsv").write_text("\n".join(raw) + "\n")
    wait_native(folder, deadline_seconds=60)
    if not (folder / "seeded.json").exists():
        case = check_directory(folder, "seed")
        tool(folder, case, "seed")
        verify(folder)
        exclusive_save(
            folder / "seeded.json",
            {"at": now(), "customers": customers, "subscribers": subscribers},
        )
    else:
        verify(folder)
    print("Dataset and preserved witness:", folder, flush=True)


def profile_benchmark():
    folder, config = fixture()
    verify(folder)
    case = (
        folder
        / "profiles"
        / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            + "-"
            + uuid.uuid4().hex[:8]
        )
    )
    case.mkdir(parents=True)
    case.chmod(0o777)
    previous = [
        json.loads(p.read_text())["version"]
        for p in (folder / "profiles").glob("*/run.json")
    ]
    operations = int(os.environ.get("SUBSCRIBER_OPERATIONS", "10000"))
    if not 1 <= operations <= config["subscribers"]:
        raise ValueError("SUBSCRIBER_OPERATIONS must fit the fixture")
    exclusive_save(
        case / "run.json",
        {"version": max(previous, default=0) + 1, "operations": operations},
    )
    (case / "phase").write_text("profiles\n")
    try:
        with (case / "workload.log").open("w") as log:
            # Compose prints the complete application failure to this fixture's log.
            env = dict(
                os.environ,
                WITNESS_DIR=str(folder.resolve()),
                RESULTS_DIR=str(case.resolve()),
            )
            subprocess.run(
                [*COMPOSE, "run", "--rm", "-T", "workload", "subscribers"],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=900,
                check=True,
            )
        full = verify(folder)
        exclusive_save(
            case / "result.json", {"at": now(), "full_historical_reconciliation": full}
        )
    except BaseException as error:
        exclusive_save(case / "failure.json", {"at": now(), "error": str(error)})
        raise
    print("Passed versioned subscriber workload:", case, flush=True)


def main():
    os.chdir(Path(__file__).resolve().parents[1])
    ROOT.mkdir(exist_ok=True)
    with (ROOT / ".controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mode = sys.argv[1]
        if mode == "up":
            setup()
        elif mode in ("test", "benchmark", "data", "sql", "management", "router"):
            scenario(mode)
        elif mode == "subscribers":
            profile_benchmark()
        elif mode == "verify":
            folder, _ = fixture()
            verify(folder)
        elif mode == "status":
            print(sql("sql0", "SELECT node_id,status FROM ndbinfo.nodes", headers=True))
            print(
                compose(
                    "exec",
                    "-T",
                    "mgm0",
                    "ndb_mgm",
                    "--ndb-connectstring=mgm0:1186,mgm1:1186",
                    "-e",
                    "show",
                )
            )
        elif mode == "down":
            folder, _ = fixture()
            verify(folder)
            sql("sql0", "DROP TABLE subscribers,orders,customers,lab_identity")
            exclusive_save(folder / "closed.json", {"at": now()})
        else:
            raise ValueError(mode)


if __name__ == "__main__":
    main()
