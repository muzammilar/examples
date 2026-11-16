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

PROJECT = "mysql-vitess-cluster"
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
    # Do not allow a later fault to conceal already missing historical ACKs.
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
    target, paused = None, False
    proof = {}
    started = time.monotonic()
    try:
        for role, action in (("reader", "watch"), ("writer", "load")):
            if role == "writer":
                # The immutable-history reader must finish loading journals
                # before this run's writer can create an in-progress JSONL tail.
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
            shard = SHARDS.index(os.environ.get("FAULT_SHARD", "0"))
            proof["affected_shard"] = SHARDS[shard]
            proof["survivors_before"] = before
            progress(case, writer, reader, markers[-1]["at"], shard=shard)
            leader = primary(shard)
            if mode in ("failover", "replica-crash", "router"):
                target = leader
                if mode == "replica-crash":
                    target = next(n for n in TABLETS[shard] if n != leader)
                elif mode == "router":
                    target = "vtgate"
                identity = owned(target)
                phase(case, "outage", markers)
                began = time.monotonic()
                compose("kill", "-s", "SIGKILL", target)
                killed = owned(target)
                if killed["State"]["Running"] or killed["State"]["ExitCode"] != 137:
                    raise RuntimeError("native container SIGKILL not proven")
                proof["killed_member"] = {
                    "node": target,
                    "id": identity["Id"],
                    "exit": killed["State"],
                }
                if mode == "failover":
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        try:
                            new = primary(shard)
                            if new != target:
                                break
                        except RuntimeError:
                            pass
                        time.sleep(1)
                    else:
                        raise RuntimeError("VTOrc did not promote a different tablet")
                    proof["promotion_seconds"] = time.monotonic() - began
                    proof["new_primary"] = new
                if mode != "router":
                    phase(case, "degraded", markers)
                    progress(case, writer, reader, markers[-1]["at"], shard=shard)
                time.sleep(10)
            elif mode == "replica-lag":
                target = next(n for n in TABLETS[shard] if n != leader)
                owned(target)
                phase(case, "outage", markers)
                compose("pause", target)
                paused = True
                state = owned(target)
                if not state["State"]["Paused"]:
                    raise RuntimeError("replica pause not proven")
                proof["paused_replica"] = {"node": target, "id": state["Id"]}
                time.sleep(10)
            else:
                raise ValueError(mode)
            phase(case, "recovery", markers)
            if paused:
                compose("unpause", target)
                paused = False
            else:
                compose(
                    "up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", target
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
                        "unrelated survivor restarted/changed: " + service
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
        if target:
            try:
                if paused:
                    compose("unpause", target)
                compose(
                    "up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", target
                )
            except Exception as error:
                cleanup_errors.append(str(error))
        if not (case / "result.json").exists():
            (case / "abort-writer").touch()
        for role in ("writer", "reader"):
            (case / ("stop-" + role)).touch()
        for role, process in processes.items():
            if process.poll() is None:
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    # Only stop a container created with this invocation's token.
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


SHARDS = ("0",)
TABLETS = {0: ("t100", "t101", "t102")}


def alias(node):
    if node not in sum(TABLETS.values(), ()):
        raise ValueError("not an owned tablet: " + node)
    return "zone1-" + f"{int(node[1:]):010d}"


def ctl(*args):
    output = compose(
        "exec",
        "-T",
        "vtctld",
        "/vt/bin/vtctldclient",
        "--server",
        "localhost:15999",
        *args,
        timeout=180,
    )
    # This CLI mixes JSON lookup responses with text action/status output.
    if args[0] in {"GetShard", "GetTablet", "GetKeyspace", "GetVSchema"}:
        return json.loads(output)
    return output


def primary(shard):
    value = ctl("GetShard", "orders/" + SHARDS[shard])["shard"]
    node = "t" + str((value.get("primary_alias") or {}).get("uid", 0))
    if node not in TABLETS[shard]:
        raise RuntimeError("no valid native primary: " + SHARDS[shard])
    return node


def sql(node, statement, *, headers=False, timeout=30):
    uid = int(alias(node).split("-")[1])
    args = [
        "exec",
        "-T",
        node,
        "mysql",
        f"--socket=/vt/vtdataroot/vt_{uid:010d}/mysql.sock",
        "-uvt_dba",
        "--batch",
    ]
    if not headers:
        args.append("--skip-column-names")
    return compose(*args, "-e", statement, timeout=timeout).rstrip("\r\n")


def gate(statement):
    return compose(
        "exec",
        "-T",
        "vtgate",
        "mysql",
        "-h127.0.0.1",
        "-P15306",
        "-uroot",
        "-Dorders@primary",
        "--batch",
        "--skip-column-names",
        "-e",
        statement,
    ).strip()


def server_uuid(node):
    return sql(node, "SELECT @@GLOBAL.server_uuid")


def identities():
    values = {}
    for shard in range(len(SHARDS)):
        node = primary(shard)
        exists = sql(
            node,
            "SELECT count(*) FROM information_schema.tables WHERE table_schema='vt_orders' AND table_name='lab_identity'",
        )
        if exists == "1":
            values[SHARDS[shard]] = json.loads(
                sql(
                    node,
                    "SELECT JSON_OBJECT('id',fixture_id,'customers',customers) FROM vt_orders.lab_identity WHERE slot=1",
                )
            )
    return values


def fixture():
    members = identities()
    if (
        len(members) != len(SHARDS)
        or len({json.dumps(v, sort_keys=True) for v in members.values()}) != 1
    ):
        raise RuntimeError("run make up; native dataset identities differ")
    value = next(iter(members.values()))
    folder = ROOT / str(uuid.UUID(value["id"]))
    config = json.loads((folder / "fixture.json").read_text())
    if config["customers"] != value["customers"]:
        raise RuntimeError("native customer identity differs from witness")
    if set(config["physical"]) != {n for nodes in TABLETS.values() for n in nodes}:
        raise RuntimeError(
            "incomplete physical identity witness; preserve and audit it"
        )
    for node, expected in config["physical"].items():
        if server_uuid(node) != expected:
            raise RuntimeError("tablet server UUID differs from witness: " + node)
    if not (folder / "seeded.json").exists():
        raise RuntimeError("resume make up to verify seed")
    return folder, config


def validate_vschema():
    config = ctl("GetVSchema", "orders")
    if config.get("v_schema"):
        config = config["v_schema"]
    if config.get("sharded") or any(
        table not in config.get("tables", {})
        or config["tables"][table].get("column_vindexes")
        for table in ("customers", "orders")
    ):
        raise RuntimeError("expected unsharded customer/orders VSchema")
    shard = ctl("GetShard", "orders/0")
    key_range = shard["shard"].get("key_range") or {}
    if shard["name"] != "0" or any(key_range.get(k) for k in ("start", "end")):
        raise RuntimeError("expected native full-range shard 0")


def setup():
    for shard in range(len(SHARDS)):
        try:
            primary(shard)
        except RuntimeError:
            ctl(
                "PlannedReparentShard",
                "--new-primary",
                alias(TABLETS[shard][0]),
                "--wait-replicas-timeout=90s",
                "orders/" + SHARDS[shard],
            )
    customers = int(os.environ.get("CUSTOMERS", "10000"))
    if not 1 <= customers <= 1000000:
        raise ValueError("CUSTOMERS must be 1..1000000")
    members = identities()
    if members:
        value = next(iter(members.values()))
        if any(v != value for v in members.values()) or value["customers"] != customers:
            raise RuntimeError("retained dataset/CUSTOMERS mismatch")
        folder = ROOT / str(uuid.UUID(value["id"]))
        recorded = json.loads((folder / "fixture.json").read_text())
        if set(recorded["physical"]) != {
            n for nodes in TABLETS.values() for n in nodes
        }:
            raise RuntimeError(
                "incomplete physical identity witness; preserve and audit it"
            )
        for node, expected in recorded["physical"].items():
            if server_uuid(node) != expected:
                raise RuntimeError("retained physical UUID changed: " + node)
    else:
        for shard in range(len(SHARDS)):
            if (
                sql(
                    primary(shard),
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema='vt_orders' AND table_name IN('customers','orders')",
                )
                != "0"
            ):
                raise RuntimeError(
                    "legacy tables lack an external witness; refusing reset/adoption"
                )
        identity = str(uuid.uuid4())
        folder = ROOT / identity
        exclusive_save(
            folder / "fixture.json",
            {
                "id": identity,
                "customers": customers,
                "physical": {
                    n: server_uuid(n) for nodes in TABLETS.values() for n in nodes
                },
                "created": now(),
            },
        )
    config = json.loads((folder / "fixture.json").read_text())
    for shard in range(len(SHARDS)):
        if SHARDS[shard] not in members:
            node = primary(shard)
            sql(
                node,
                "CREATE DATABASE IF NOT EXISTS vt_orders; CREATE TABLE vt_orders.lab_identity(slot int PRIMARY KEY,fixture_id char(36) NOT NULL,customers int NOT NULL); "
                + f"INSERT INTO vt_orders.lab_identity VALUES(1,'{config['id']}',{customers})",
            )
    ctl("ApplySchema", "--sql-file=/sql/schema.sql", "orders")
    ctl("ApplyVSchema", "--vschema-file=/config/vschema.json", "orders")
    ctl("ValidateSchemaKeyspace", "orders")
    validate_vschema()
    compose(
        "--profile",
        "recovery",
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        "180",
        "vtorc",
        timeout=240,
    )
    deadline = time.monotonic() + 120
    while True:
        try:
            gate("SELECT count(*) FROM customers")
            break
        except RuntimeError:
            if time.monotonic() > deadline:
                raise
            time.sleep(1)
    if not (folder / "routing.json").exists():
        # One unsharded keyspace; this is not a hash/placement oracle.
        exclusive_save(folder / "routing.json", [0] * customers)
    if not (folder / "seeded.json").exists():
        tool(folder, check_directory(folder, "seed"), "seed")
        verify(folder)
        exclusive_save(folder / "seeded.json", {"at": now(), "customers": customers})
    else:
        verify(folder)
    print("Dataset and retained witness:", folder, flush=True)


def replica_status(node):
    lines = sql(node, "SHOW REPLICA STATUS", headers=True).splitlines()
    if len(lines) != 2:
        raise RuntimeError("expected one native replication channel: " + node)
    return dict(zip(lines[0].split("\t"), lines[1].split("\t"), strict=True))


def native_state(node):
    values = sql(
        node,
        "SELECT @@GLOBAL.server_uuid,@@GLOBAL.read_only,@@GLOBAL.super_read_only,@@GLOBAL.innodb_flush_log_at_trx_commit,@@GLOBAL.sync_binlog,@@GLOBAL.sync_relay_log,@@GLOBAL.gtid_mode,@@GLOBAL.pid_file",
    ).split("\t")
    state = dict(
        zip(
            (
                "uuid",
                "read_only",
                "super_read_only",
                "innodb_flush_log_at_trx_commit",
                "sync_binlog",
                "sync_relay_log",
                "gtid_mode",
                "pid_file",
            ),
            values,
            strict=True,
        )
    )
    pid = int(compose("exec", "-T", node, "cat", state["pid_file"]).strip())
    stat = compose("exec", "-T", node, "cat", f"/proc/{pid}/stat")
    state["mysqld_pid"] = pid
    state["mysqld_started"] = stat.rsplit(")", 1)[1].split()[19]
    return state


def wait_native(folder, deadline_seconds=240):
    config = json.loads((folder / "fixture.json").read_text())
    targets = {
        i: {
            "primary": (p := primary(i)),
            "gtid": sql(p, "SELECT @@GLOBAL.gtid_executed"),
        }
        for i in range(len(SHARDS))
    }
    deadline = time.monotonic() + deadline_seconds
    last = None
    while time.monotonic() < deadline:
        try:
            if (
                ctl("GetKeyspace", "orders")["keyspace"]["durability_policy"]
                != "semi_sync"
            ):
                raise RuntimeError("native durability policy changed")
            result = {}
            for shard, target in targets.items():
                leader = target["primary"]
                if primary(shard) != leader:
                    raise RuntimeError("primary changed during frozen GTID barrier")
                status = dict(
                    row.split("\t")
                    for row in sql(
                        leader,
                        "SHOW GLOBAL STATUS WHERE Variable_name IN('Rpl_semi_sync_source_status','Rpl_semi_sync_master_status','Rpl_semi_sync_source_clients','Rpl_semi_sync_master_clients')",
                    ).splitlines()
                )
                if not any(
                    v == "ON"
                    for k, v in status.items()
                    if k.lower().endswith("_status")
                ) or not any(
                    int(v) >= 1
                    for k, v in status.items()
                    if k.lower().endswith("_clients")
                ):
                    raise RuntimeError(
                        "native semi-sync primary lacks a healthy acknowledgement peer"
                    )
                semi_sync_config = dict(
                    row.split("\t")
                    for row in sql(
                        leader,
                        "SHOW GLOBAL VARIABLES WHERE Variable_name IN('rpl_semi_sync_source_timeout','rpl_semi_sync_source_wait_for_replica_count','rpl_semi_sync_source_wait_no_replica','rpl_semi_sync_source_wait_point')",
                    ).splitlines()
                )
                if semi_sync_config != {
                    "rpl_semi_sync_source_timeout": "1000000000000000000",
                    "rpl_semi_sync_source_wait_for_replica_count": "1",
                    "rpl_semi_sync_source_wait_no_replica": "ON",
                    "rpl_semi_sync_source_wait_point": "AFTER_SYNC",
                }:
                    raise RuntimeError(
                        "native semi-sync acknowledgement settings changed"
                    )
                nodes = {}
                for node in TABLETS[shard]:
                    if node != leader:
                        if (
                            sql(
                                node,
                                f"SELECT WAIT_FOR_EXECUTED_GTID_SET('{target['gtid']}',30)",
                                timeout=40,
                            )
                            != "0"
                        ):
                            raise RuntimeError(
                                "replica behind frozen GTID barrier: " + node
                            )
                    state = native_state(node)
                    if state["uuid"] != config["physical"][node] or state[
                        "read_only"
                    ] != ("0" if node == leader else "1"):
                        raise RuntimeError("wrong physical tablet/role: " + node)
                    if (
                        any(
                            state[k] != "1"
                            for k in (
                                "innodb_flush_log_at_trx_commit",
                                "sync_binlog",
                                "sync_relay_log",
                            )
                        )
                        or state["gtid_mode"] != "ON"
                    ):
                        raise RuntimeError(
                            "native durability settings changed: " + node
                        )
                    marker = json.loads(
                        sql(
                            node,
                            "SELECT JSON_OBJECT('id',fixture_id,'customers',customers) FROM vt_orders.lab_identity WHERE slot=1",
                        )
                    )
                    if marker != {"id": config["id"], "customers": config["customers"]}:
                        raise RuntimeError("tablet dataset identity differs")
                    topology = ctl("GetTablet", alias(node))
                    topology = topology.get("tablet", topology)
                    expected_type = (1, "PRIMARY") if node == leader else (2, "REPLICA")
                    if topology["type"] not in expected_type:
                        raise RuntimeError("tablet topology role differs: " + node)
                    if node != leader:
                        repl = replica_status(node)
                        if (
                            repl.get("Replica_IO_Running", repl.get("Slave_IO_Running"))
                            != "Yes"
                            or repl.get(
                                "Replica_SQL_Running", repl.get("Slave_SQL_Running")
                            )
                            != "Yes"
                            or repl.get("Source_UUID", repl.get("Master_UUID"))
                            != config["physical"][leader]
                        ):
                            raise RuntimeError(
                                "replica not streaming from native primary: " + node
                            )
                        state["replication"] = repl
                    health = json.loads(
                        compose(
                            "exec",
                            "-T",
                            node,
                            "curl",
                            "-fsS",
                            "--max-time",
                            "3",
                            "http://localhost:8080/debug/vars",
                        )
                    )
                    if health.get("TabletStateName") != "SERVING":
                        raise RuntimeError(
                            "tablet query service is not SERVING: " + node
                        )
                    state["tablet_serving"] = health["TabletStateName"]
                    nodes[node] = state
                result[SHARDS[shard]] = {
                    "primary": leader,
                    "frozen_gtid": target["gtid"],
                    "semi_sync": status,
                    "semi_sync_config": semi_sync_config,
                    "members": nodes,
                }
            return result
        except RuntimeError as error:
            last = str(error)
            time.sleep(1)
    raise RuntimeError("native repair did not finish: " + str(last))


def tool(folder, case, action, *, node=None, shard=None, capture=False):
    args = ["run", "--rm", "-T"]
    if node:
        info = owned(node)
        mounts = [
            m
            for m in info["Mounts"]
            if m["Destination"] == "/vt/vtdataroot" and m["Type"] == "volume"
        ]
        if len(mounts) != 1 or mounts[0]["Name"] != PROJECT + "_" + node:
            raise RuntimeError("refusing unknown tablet data mount")
        uid = int(node[1:])
        args += [
            "--user",
            "999:999",  # Pinned MySQL image owns its 0750 socket directory as UID 999.
            "--volume",
            mounts[0]["Name"] + ":/tablet:ro",
            "-e",
            f"DSN=vt_dba@unix(/tablet/vt_{uid:010d}/mysql.sock)/vt_orders?interpolateParams=true&timeout=2s&readTimeout=15s&writeTimeout=15s",
            "-e",
            "VITESS=0",
        ]
    if shard is not None:
        args += ["-e", "EXPECTED_SHARD=" + str(shard)]
    env = dict(
        os.environ, WITNESS_DIR=str(folder.resolve()), RESULTS_DIR=str(case.resolve())
    )
    return compose(*args, "workload", action, capture=capture, timeout=900, env=env)


def verify(folder):
    native = wait_native(folder)
    validate_vschema()
    reports = {}
    case = check_directory(folder, "router")
    tool(folder, case, "verify")
    reports["router"] = json.loads((case / "check.json").read_text())
    for shard, nodes in TABLETS.items():
        for node in nodes:
            case = check_directory(folder, node)
            tool(folder, case, "verify", node=node, shard=shard)
            reports[node] = json.loads((case / "check.json").read_text())
    result = {"at": now(), "native": native, "copies": reports}
    exclusive_save(
        folder / "checks" / ("full-" + str(time.time_ns()) + ".json"), result
    )
    print(
        "Verified VTGate and all three native MySQL copies:",
        reports["router"],
        flush=True,
    )
    return result


def snapshot():
    result = {}
    for service in (
        "etcd0",
        "etcd1",
        "etcd2",
        "vtctld",
        "vtgate",
        "vtorc",
        *(n for nodes in TABLETS.values() for n in nodes),
    ):
        info = owned(service)
        result[service] = {
            "id": info["Id"],
            "started": info["State"]["StartedAt"],
            "restarts": info["RestartCount"],
            "running": info["State"]["Running"],
            "paused": info["State"]["Paused"],
        }
        if service.startswith("t"):
            state = native_state(service)
            result[service].update(
                mysqld_pid=state["mysqld_pid"], mysqld_started=state["mysqld_started"]
            )
    return result


def main():
    os.chdir(Path(__file__).resolve().parent)
    ROOT.mkdir(exist_ok=True)
    with (ROOT / ".controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        mode = sys.argv[1]
        if mode == "up":
            setup()
        elif mode in (
            "test",
            "benchmark",
            "failover",
            "replica-lag",
            "replica-crash",
            "router",
        ):
            scenario(mode)
        elif mode == "verify":
            folder, _ = fixture()
            verify(folder)
        elif mode == "status":
            print(ctl("GetTablets", "--keyspace", "orders"))
        elif mode == "down":
            folder, _ = fixture()
            verify(folder)
            compose(
                "exec",
                "-T",
                "vtgate",
                "mysql",
                "-h127.0.0.1",
                "-P15306",
                "-uroot",
                "-Dorders@primary",
                "-e",
                "DROP TABLE orders,customers",
            )
            for shard in range(len(SHARDS)):
                sql(primary(shard), "DROP TABLE vt_orders.lab_identity")
            exclusive_save(folder / "closed.json", {"at": now()})
        else:
            raise ValueError(mode)


if __name__ == "__main__":
    main()
