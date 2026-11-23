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

PROJECT = "postgres-citus-orders"
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


def sql(node, statement, *, timeout=30):
    return compose(
        "exec",
        "-T",
        node,
        "psql",
        "-X",
        "-qAt",
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        statement,
        timeout=timeout,
    ).strip()


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


def primary(shard):
    found = []
    for replica in range(3):
        node = f"s{shard}n{replica}"
        status = compose(
            "exec",
            "-T",
            node,
            "curl",
            "-fsS",
            "--max-time",
            "2",
            "http://localhost:8008/primary",
            check=False,
            timeout=5,
        )
        if status.returncode == 0:
            found.append(node)
    if len(found) != 1:
        raise RuntimeError(f"expected one native primary for shard {shard}: {found}")
    return found[0]


def tool(folder, case, action, *, node=None, shard=None, extra=None, capture=False):
    args = ["run", "--rm", "-T"]
    if node:
        args += [
            "-e",
            f"DSN=postgres://postgres:local-postgres@{node}:5432/postgres?sslmode=disable",
        ]
    if shard is not None:
        args += ["-e", "EXPECTED_SHARD=" + str(shard)]
    for name, value in (extra or {}).items():
        args += ["-e", name + "=" + value]
    env = dict(
        os.environ, WITNESS_DIR=str(folder.resolve()), RESULTS_DIR=str(case.resolve())
    )
    return compose(*args, "workload", action, capture=capture, timeout=900, env=env)


def check_directory(folder, name):
    result = folder / "checks" / (name + "-" + str(time.time_ns()))
    result.mkdir(parents=True)
    result.chmod(0o777)
    return result


def native_state(node):
    return json.loads(
        sql(
            node,
            "SELECT json_build_object('system_id',(SELECT system_identifier::text FROM pg_control_system()),'replica',pg_is_in_recovery(),'started',pg_postmaster_start_time(),'lsn',CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn() ELSE pg_current_wal_flush_lsn() END)",
        )
    )


def read_events(path):
    return (
        [json.loads(line) for line in path.read_text().split("\n")[:-1]]
        if path.exists()
        else []
    )


def progress(case, writer, reader, after, *, writes=True, shards=None, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if reader.poll() is not None or (writes and writer.poll() is not None):
            raise RuntimeError("live writer/reader exited before native recovery")
        reads = sum(
            e["outcome"] == "ok"
            and timestamp(e["at"]) > timestamp(after)
            and (shards is None or e["shard"] in shards)
            for e in read_events(case / "reads.jsonl")
        )
        acks = sum(
            e["stage"] == "ack"
            and timestamp(e["at"]) > timestamp(after)
            and (shards is None or e["shard"] in shards)
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
            shard = int(os.environ.get("FAULT_SHARD", "1"))
            if shard not in groups():
                raise ValueError("FAULT_SHARD must be an active native group")
            affected = (
                None
                if shard == 0
                else {r["orders"] for r in placements() if r["group"] == shard}
            )
            proof["survivors_before"] = before
            proof["affected_group"] = shard
            proof["affected_logical_shards"] = sorted(affected) if affected else None
            changed = set()
            leader = primary(shard)
            if mode in ("scale-out", "scale-in"):
                phase(case, mode, markers)
                scale(folder, mode, proof)
                if mode == "scale-in":
                    changed = {f"s{g}n{r}" for g in (4, 5) for r in range(3)}
            elif mode == "failover":
                target = leader
                identity = owned(target)
                phase(case, "outage", markers)
                began = time.monotonic()
                compose("kill", "-s", "SIGKILL", target)
                killed = owned(target)
                if killed["State"]["Running"] or killed["State"]["ExitCode"] != 137:
                    raise RuntimeError("native container SIGKILL not proven")
                proof["old_primary"] = {
                    "node": target,
                    "id": identity["Id"],
                    "exit": killed["State"],
                }
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    try:
                        new = primary(shard)
                        if new != target:
                            break
                    except RuntimeError:
                        pass
                    time.sleep(1)
                else:
                    raise RuntimeError("no different primary elected before deadline")
                proof["promotion_seconds"] = time.monotonic() - began
                proof["new_primary"] = new
                phase(case, "degraded", markers)
                progress(case, writer, reader, markers[-1]["at"], shards=affected)
                time.sleep(10)
            elif mode == "replica-lag":
                target = sql(
                    leader,
                    "SELECT application_name FROM pg_stat_replication WHERE sync_state='sync'",
                )
                if not re.fullmatch(f"s{shard}n[012]", target) or target == leader:
                    raise RuntimeError("no unique synchronous replica")
                owned(target)
                phase(case, "outage", markers)
                compose("pause", target)
                paused = True
                state = owned(target)
                if not state["State"]["Paused"]:
                    raise RuntimeError("replica pause not proven")
                proof["paused_sync_replica"] = {"node": target, "id": state["Id"]}
                time.sleep(10)
            else:
                raise ValueError(mode)
            phase(case, "recovery", markers)
            if paused:
                compose("unpause", target)
                paused = False
            elif target:
                compose(
                    "up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", target
                )
            repair_started = time.monotonic()
            proof["native_repair_under_load"] = wait_native(folder)
            proof["native_repair_barrier_seconds"] = time.monotonic() - repair_started
            proof["native_repair_completed_at"] = now()
            progress(case, writer, reader, markers[-1]["at"], shards=affected)
            after = snapshot()
            proof["survivors_after"] = after
            for service, identity in before.items():
                if (
                    service != target
                    and service not in changed
                    and after.get(service) != identity
                ):
                    raise RuntimeError(
                        "unrelated survivor restarted/changed: " + service
                    )
            proof["unchanged_survivors"] = [
                s for s in before if s != target and s not in changed
            ]
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


def groups():
    values = sql(
        primary(0),
        "SELECT DISTINCT groupid FROM pg_dist_node WHERE isactive AND noderole='primary' ORDER BY groupid",
    )
    result = [int(value) for value in values.splitlines()]
    if result not in ([0, 1, 2, 3], [0, 1, 2, 3, 4, 5]):
        raise RuntimeError(
            "expected coordinator plus three/five native worker groups: " + str(result)
        )
    return result


def physical_identity(group):
    return sql(
        primary(group), "SELECT system_identifier::text FROM pg_control_system()"
    )


def expected_identity(folder, group):
    initial = json.loads((folder / "fixture.json").read_text())["physical"]
    return (
        initial[str(group)]
        if str(group) in initial
        else json.loads((folder / "groups" / (str(group) + ".json")).read_text())[
            "physical"
        ]
    )


def fixture():
    node = primary(0)
    value = json.loads(
        sql(
            node,
            "SELECT json_build_object('id',fixture_id::text,'customers',customers) FROM lab_identity WHERE slot=1",
        )
    )
    folder = ROOT / str(uuid.UUID(value["id"]))
    recorded = json.loads((folder / "fixture.json").read_text())
    if recorded["customers"] != value["customers"]:
        raise RuntimeError("customer identity differs from external witness")
    for group in groups():
        if physical_identity(group) != expected_identity(folder, group):
            raise RuntimeError(
                "physical group no longer matches its witness: " + str(group)
            )
    if not (folder / "seeded.json").exists():
        raise RuntimeError("resume make up before workloads")
    return folder, recorded


def setup():
    members = groups()
    node = primary(0)
    customers = int(os.environ.get("CUSTOMERS", "10000"))
    if not 1 <= customers <= 1000000:
        raise ValueError("CUSTOMERS must be 1..1000000")
    if sql(node, "SELECT to_regclass('public.lab_identity') IS NOT NULL") == "t":
        value = json.loads(
            sql(
                node,
                "SELECT json_build_object('id',fixture_id::text,'customers',customers) FROM lab_identity WHERE slot=1",
            )
        )
        if value["customers"] != customers:
            raise RuntimeError("CUSTOMERS differs from retained fixture")
        folder = ROOT / str(uuid.UUID(value["id"]))
        for group in members:
            if physical_identity(group) != expected_identity(folder, group):
                raise RuntimeError("physical group differs from retained witness")
    else:
        if (
            sql(
                node,
                "SELECT to_regclass('public.orders') IS NOT NULL OR to_regclass('public.customers') IS NOT NULL OR to_regclass('public.customer_profiles') IS NOT NULL",
            )
            != "f"
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
                "physical": {str(g): physical_identity(g) for g in members},
                "created": now(),
            },
        )
        sql(
            node,
            "CREATE TABLE lab_identity(slot int PRIMARY KEY CHECK(slot=1),fixture_id uuid NOT NULL,customers int NOT NULL); "
            + f"INSERT INTO lab_identity VALUES(1,'{identity}',{customers})",
        )
    # Resume only schema owned by this already identified dataset.
    sql(node, Path("sql/schema.sql").read_text())
    for table in ("customers", "orders", "customer_profiles"):
        if (
            sql(
                node,
                f"SELECT count(*) FROM pg_dist_partition WHERE logicalrelid='{table}'::regclass",
            )
            == "0"
        ):
            colocate = "" if table == "customers" else ",colocate_with=>'customers'"
            sql(
                node,
                f"SET citus.shard_count=30; SELECT create_distributed_table('{table}','customer_id'{colocate})",
            )
    if not (folder / "routing.json").exists():
        routes = json.loads(
            sql(
                node,
                f"SELECT json_agg(get_shard_id_for_distribution_column('orders',c::bigint) ORDER BY c) FROM generate_series(0,{customers - 1}) c",
            )
        )
        if len(routes) != customers or len(set(routes)) > 30:
            raise RuntimeError("native routing oracle incomplete")
        exclusive_save(folder / "routing.json", routes)
    if not (folder / "seeded.json").exists():
        tool(folder, check_directory(folder, "seed"), "seed")
        verify(folder)
        exclusive_save(folder / "seeded.json", {"at": now(), "customers": customers})
    else:
        verify(folder)
    print("Dataset and retained witness:", folder, flush=True)


def placements():
    query = """SELECT coalesce(json_agg(json_build_object('orders',s.shardid,'customers',c.shardid,'profiles',f.shardid,'group',p.groupid) ORDER BY s.shardid),'[]')
    FROM pg_dist_shard s JOIN pg_dist_placement p ON p.shardid=s.shardid AND p.shardstate=1
    JOIN pg_dist_shard c ON c.logicalrelid='customers'::regclass AND c.shardminvalue=s.shardminvalue AND c.shardmaxvalue=s.shardmaxvalue
    JOIN pg_dist_shard f ON f.logicalrelid='customer_profiles'::regclass AND f.shardminvalue=s.shardminvalue AND f.shardmaxvalue=s.shardmaxvalue
    JOIN pg_dist_placement cp ON cp.shardid=c.shardid AND cp.groupid=p.groupid AND cp.shardstate=1
    JOIN pg_dist_placement fp ON fp.shardid=f.shardid AND fp.groupid=p.groupid AND fp.shardstate=1
    WHERE s.logicalrelid='orders'::regclass"""
    rows = json.loads(sql(primary(0), query))
    validate_placements(rows, groups()[1:])
    return rows


def validate_placements(rows, workers):
    if (
        len(rows) != 30
        or any(
            len({r[t] for r in rows}) != 30 for t in ("orders", "customers", "profiles")
        )
        or {r["group"] for r in rows} != set(workers)
    ):
        raise RuntimeError(
            "expected 30 unique colocated native shard placements across every worker group"
        )


def metadata_signature(node):
    return json.loads(
        sql(
            node,
            "SELECT json_agg(json_build_array(shardid,groupid,shardstate) ORDER BY shardid,groupid) FROM pg_dist_placement WHERE shardid IN(SELECT shardid FROM pg_dist_shard WHERE logicalrelid IN('orders'::regclass,'customers'::regclass,'customer_profiles'::regclass))",
        )
    )


def wait_native(folder, deadline_seconds=180):
    members = groups()
    targets = {
        str(g): {
            "primary": (p := primary(g)),
            "lsn": sql(p, "SELECT pg_current_wal_flush_lsn()::text"),
        }
        for g in members
    }
    deadline = time.monotonic() + deadline_seconds
    last = None
    while time.monotonic() < deadline:
        try:
            if groups() != members:
                raise RuntimeError("membership changed during frozen WAL barrier")
            result = {}
            for group in members:
                target = targets[str(group)]
                if primary(group) != target["primary"]:
                    raise RuntimeError("primary changed during frozen WAL barrier")
                expected = {f"s{group}n{r}" for r in range(3)} - {target["primary"]}
                peers = json.loads(
                    sql(
                        target["primary"],
                        "SELECT coalesce(json_agg(json_build_object('node',application_name,'state',state,'sync',sync_state)),'[]') FROM pg_stat_replication",
                    )
                )
                if {
                    p["node"] for p in peers if p["state"] == "streaming"
                } != expected or not any(p["sync"] == "sync" for p in peers):
                    raise RuntimeError("native redundancy incomplete")
                native_name = sql(
                    primary(0),
                    f"SELECT nodename FROM pg_dist_node WHERE groupid={group} AND isactive AND noderole='primary'",
                )
                if native_name != target["primary"]:
                    raise RuntimeError("Citus has not learned promoted primary")
                states = {}
                for replica in range(3):
                    node = f"s{group}n{replica}"
                    state = native_state(node)
                    if state["system_id"] != expected_identity(folder, group) or state[
                        "replica"
                    ] != (node != target["primary"]):
                        raise RuntimeError("wrong physical member/role: " + node)
                    if (
                        state["replica"]
                        and sql(
                            node,
                            f"SELECT coalesce(pg_last_wal_replay_lsn()>='{target['lsn']}'::pg_lsn,false)",
                        )
                        != "t"
                    ):
                        raise RuntimeError("member behind frozen WAL barrier: " + node)
                    states[node] = state
                result[str(group)] = {
                    "primary": target["primary"],
                    "frozen_lsn": target["lsn"],
                    "peers": peers,
                    "members": states,
                }
            return result
        except RuntimeError as error:
            last = str(error)
            time.sleep(1)
    raise RuntimeError("native recovery deadline: " + str(last))


def physical_queries(local):
    queries = {"OWNED_SHARDS": json.dumps([r["orders"] for r in local])}
    for variable, table, key, columns in (
        ("ORDER_QUERY", "orders", "orders", "op_id,customer_id,amount"),
        ("CUSTOMER_QUERY", "customers", "customers", "customer_id,name"),
        (
            "PROFILE_QUERY",
            "customer_profiles",
            "profiles",
            "customer_id,version,display_name",
        ),
    ):
        queries[variable] = " UNION ALL ".join(
            f"SELECT {columns},{r['orders']}::bigint FROM public.{table}_{r[key]}"
            for r in local
        )
    return queries


def verify(folder):
    native = wait_native(folder)
    mapping = placements()
    reports = {}
    metadata = {}
    case = check_directory(folder, "coordinator")
    tool(folder, case, "verify")
    reports["router"] = json.loads((case / "check.json").read_text())
    reference = metadata_signature(primary(0))
    for replica in range(3):
        node = f"s0n{replica}"
        metadata[node] = metadata_signature(node)
        if metadata[node] != reference:
            raise RuntimeError("coordinator metadata copy differs: " + node)
    for group in groups()[1:]:
        local = [r for r in mapping if r["group"] == group]
        expected = sorted(
            f"{table}_{r[key]}"
            for r in local
            for table, key in (
                ("orders", "orders"),
                ("customers", "customers"),
                ("customer_profiles", "profiles"),
            )
        )
        for replica in range(3):
            node = f"s{group}n{replica}"
            actual = sql(
                node,
                "SET citus.show_shards_for_app_name_prefixes='psql'; SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relname~'^(orders|customers|customer_profiles)_[0-9]+$' ORDER BY relname",
            ).splitlines()
            if sorted(actual) != expected:
                raise RuntimeError(
                    "physical shard inventory differs, including orphan tables: " + node
                )
            queries = physical_queries(local)
            case = check_directory(folder, node)
            tool(folder, case, "verify", node=node, shard=group, extra=queries)
            reports[node] = json.loads((case / "check.json").read_text())
    if mapping != placements():
        raise RuntimeError("ownership changed during full physical verification")
    result = {
        "at": now(),
        "native": native,
        "placements": mapping,
        "coordinator_metadata": metadata,
        "copies": reports,
    }
    exclusive_save(
        folder / "checks" / ("full-" + str(time.time_ns()) + ".json"), result
    )
    print(
        "Verified coordinator, native placement and every worker copy:",
        reports["router"],
        flush=True,
    )
    return result


def snapshot():
    result = {}
    for service in [
        *(f"etcd{i}" for i in range(3)),
        "router",
        *(f"s{g}n{r}" for g in groups() for r in range(3)),
    ]:
        info = owned(service)
        result[service] = {
            "id": info["Id"],
            "started": info["State"]["StartedAt"],
            "restarts": info["RestartCount"],
            "running": info["State"]["Running"],
            "paused": info["State"]["Paused"],
        }
        if re.fullmatch(r"s[0-5]n[012]", service):
            result[service]["postmaster_started"] = native_state(service)["started"]
    return result


def cleanup_shards():
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        sql(primary(0), "CALL citus_cleanup_orphaned_resources()", timeout=90)
        if sql(primary(0), "SELECT count(*) FROM pg_dist_cleanup") == "0":
            return
        time.sleep(1)
    raise RuntimeError("native deferred shard cleanup did not finish")


def scale(folder, mode, proof):
    proof["placements_before"] = placements()
    started = time.monotonic()
    if mode == "scale-out":
        if groups() != [0, 1, 2, 3]:
            raise RuntimeError("scale-out requires three worker groups")
        compose(
            "--profile",
            "scale",
            "up",
            "-d",
            "--wait",
            "--wait-timeout",
            "240",
            *(f"s{g}n{r}" for g in (4, 5) for r in range(3)),
            timeout=300,
        )
        deadline = time.monotonic() + 90
        while True:
            try:
                if groups() == [0, 1, 2, 3, 4, 5]:
                    break
            except RuntimeError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError("five worker groups not registered")
            time.sleep(1)
        for group in (4, 5):
            value = physical_identity(group)
            path = folder / "groups" / (str(group) + ".json")
            if path.exists():
                if json.loads(path.read_text())["physical"] != value:
                    raise RuntimeError("reused scale group lost its physical identity")
            else:
                exclusive_save(path, {"group": group, "physical": value, "at": now()})
        proof["rebalance_plan"] = json.loads(
            sql(
                primary(0),
                "SELECT coalesce(json_agg(row_to_json(p)),'[]') FROM get_rebalance_table_shards_plan() p",
            )
        )
        sql(primary(0), "SELECT citus_rebalance_start()")
        sql(primary(0), "SELECT citus_rebalance_wait()", timeout=900)
        proof["rebalance_status"] = json.loads(
            sql(
                primary(0),
                "SELECT coalesce(json_agg(row_to_json(s)),'[]') FROM citus_rebalance_status() s",
            )
        )
        if (
            not proof["rebalance_status"]
            or max(proof["rebalance_status"], key=lambda row: row["job_id"])["state"]
            != "finished"
        ):
            raise RuntimeError("native rebalance did not finish successfully")
        cleanup_shards()
    else:
        if groups() != [0, 1, 2, 3, 4, 5]:
            raise RuntimeError("scale-in requires five worker groups")
        proof["stopped_empty_members"] = {}
        for group in (4, 5):
            worker = primary(group)
            sql(primary(0), f"SELECT citus_drain_node('{worker}',5432)", timeout=900)
            cleanup_shards()
            if (
                sql(
                    primary(0),
                    f"SELECT count(*) FROM pg_dist_placement WHERE groupid={group}",
                )
                != "0"
            ):
                raise RuntimeError("refusing to remove a nonempty placement group")
            # Cleanup DDL is synchronous to one standby; wait for the other
            # physical copy too before declaring every removed member empty.
            frozen_lsn = sql(worker, "SELECT pg_current_wal_flush_lsn()::text")
            proof.setdefault("drain_replay_barriers", {})[str(group)] = {
                "primary": worker,
                "lsn": frozen_lsn,
            }
            deadline = time.monotonic() + 120
            for replica in range(3):
                node = f"s{group}n{replica}"
                owned(node)
                if node != worker:
                    while (
                        sql(
                            node,
                            f"SELECT coalesce(pg_is_in_recovery() AND pg_last_wal_replay_lsn()>='{frozen_lsn}'::pg_lsn,false)",
                        )
                        != "t"
                    ):
                        if time.monotonic() > deadline:
                            raise RuntimeError(
                                "drained replica did not replay cleanup: " + node
                            )
                        time.sleep(1)
                if (
                    sql(
                        node,
                        "SET citus.show_shards_for_app_name_prefixes='psql'; SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname~'^(orders|customers|customer_profiles)_[0-9]+$'",
                    )
                    != "0"
                ):
                    raise RuntimeError("drained physical copy still has shard tables")
            sql(primary(0), f"SELECT citus_disable_node('{worker}',5432)")
            compose("--profile", "scale", "stop", *(f"s{group}n{r}" for r in range(3)))
            for replica in range(3):
                node = f"s{group}n{replica}"
                state = owned(node)
                if state["State"]["Running"]:
                    raise RuntimeError("removed group is not stopped")
                proof["stopped_empty_members"][node] = {
                    "id": state["Id"],
                    "state": state["State"],
                }
                sql(
                    primary(0),
                    f"SELECT citus_remove_node(nodename,nodeport) FROM pg_dist_node WHERE nodename='{node}' AND nodeport=5432",
                )
    proof["operation_seconds"] = time.monotonic() - started
    proof["placements_after"] = placements()
    if mode == "scale-out" and proof["placements_after"] == proof["placements_before"]:
        raise RuntimeError("no native shard ownership changed")


def analytics_case():
    folder, config = fixture()
    report = verify(folder)
    if report["copies"]["router"]["committed_without_ack"]:
        raise RuntimeError("analytics requires fully witnessed order totals")
    previous = [
        json.loads(p.read_text())["version"]
        for p in (folder / "analytics").glob("*/analytics.json")
    ]
    case = folder / "analytics" / str(uuid.uuid4())
    case.mkdir(parents=True)
    case.chmod(0o777)
    exclusive_save(
        case / "analytics.json",
        {
            "version": max(previous, default=0) + 1,
            "requests": int(os.environ.get("ANALYTICS_REQUESTS", "200")),
        },
    )
    markers = []
    phase(case, "analytics", markers)
    try:
        tool(folder, case, "analytics")
        proof = verify(folder)
        exclusive_save(case / "result.json", {"at": now(), "verification": proof})
    except BaseException as error:
        exclusive_save(case / "failure.json", {"at": now(), "error": str(error)})
        raise
    print("Tenant analytics evidence:", case, flush=True)


def main():
    os.chdir(Path(__file__).resolve().parents[1])
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
            "scale-out",
            "scale-in",
        ):
            scenario(mode)
        elif mode == "analytics":
            analytics_case()
        elif mode == "verify":
            folder, _ = fixture()
            verify(folder)
        elif mode == "status":
            print(
                sql(
                    primary(0),
                    "SELECT json_agg(json_build_object('group',groupid,'node',nodename,'role',noderole,'active',isactive) ORDER BY groupid,nodeid) FROM pg_dist_node",
                )
            )
            print(json.dumps(placements(), indent=2))
        elif mode == "down":
            folder, _ = fixture()
            verify(folder)
            sql(
                primary(0), "DROP TABLE customer_profiles,orders,customers,lab_identity"
            )
            exclusive_save(folder / "closed.json", {"at": now()})
        else:
            raise ValueError(mode)


if __name__ == "__main__":
    main()
