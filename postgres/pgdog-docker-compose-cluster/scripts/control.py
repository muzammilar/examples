"""One example's lifecycle, native faults and historical correctness checks."""

from datetime import datetime, timezone
import csv
import io
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib
import uuid

from report import observations, timestamp

PROJECT = "postgres-pgdog-pooling"
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


def sql(node, statement):
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
        timeout=30,
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


def identities():
    members, physical = {}, {}
    for shard in range(1):
        node = primary(shard)
        physical[str(shard)] = sql(
            node, "SELECT system_identifier::text FROM pg_control_system()"
        )
        if sql(node, "SELECT to_regclass('public.lab_identity') IS NOT NULL") == "t":
            value = sql(
                node,
                "SELECT json_build_object('id',fixture_id::text,'customers',customers) FROM lab_identity WHERE slot=1",
            )
            if not value:
                raise RuntimeError("empty identity table; refusing implicit recovery")
            members[node] = json.loads(value)
    return members, physical


def fixture():
    members, physical = identities()
    if (
        len(members) != 1
        or len({json.dumps(v, sort_keys=True) for v in members.values()}) != 1
    ):
        raise RuntimeError(
            "run make up; native dataset identities must agree on the PostgreSQL group"
        )
    value = next(iter(members.values()))
    folder = ROOT / str(uuid.UUID(value["id"]))
    recorded = json.loads((folder / "fixture.json").read_text())
    if recorded["physical"] != physical or recorded["customers"] != value["customers"]:
        raise RuntimeError(
            "native dataset differs from the external witness; never reuse it"
        )
    if not (folder / "seeded.json").exists():
        raise RuntimeError("customer seed not verified; resume make up")
    return folder, recorded


def tool(folder, case, action, *, node=None, shard=None, capture=False):
    args = ["run", "--rm", "-T"]
    if node:
        args += [
            "-e",
            f"DSN=postgres://postgres:local-postgres@{node}:5432/postgres?sslmode=disable",
        ]
    if shard is not None:
        args += ["-e", "EXPECTED_SHARD=" + str(shard)]
    env = dict(
        os.environ, WITNESS_DIR=str(folder.resolve()), RESULTS_DIR=str(case.resolve())
    )
    return compose(*args, "workload", action, capture=capture, timeout=900, env=env)


def check_directory(folder, name):
    result = folder / "checks" / (name + "-" + str(time.time_ns()))
    result.mkdir(parents=True)
    result.chmod(0o777)
    return result


def validate_routing_config():
    config = tomllib.loads(Path("config/pgdog.toml").read_text())
    if config["general"]["pooler_mode"] != "transaction" or config.get(
        "sharded_tables"
    ):
        raise RuntimeError(
            "this experiment requires transaction pooling without sharding"
        )


def admin(statement, *, csv_output=False):
    return compose(
        "run",
        "--rm",
        "-T",
        "-e",
        "PGPASSWORD=local-admin",
        "client",
        "psql",
        "-X",
        *(["-q", "--csv"] if csv_output else ["-qAt", "-F", "|"]),
        "-h",
        "pgdog",
        "-p",
        "6432",
        "-U",
        "admin",
        "-d",
        "admin",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        statement,
    )


def pool_snapshot():
    rows = list(csv.DictReader(io.StringIO(admin("SHOW POOLS", csv_output=True))))
    rows = [row for row in rows if row["database"] == "orders"]
    if len(rows) != 3 or {row["addr"] for row in rows} != {"s0n0", "s0n1", "s0n2"}:
        raise RuntimeError("native pool membership is incomplete")
    return rows


def assert_pool_cap(rows, maximum):
    if any(int(row["sv_total"]) > maximum for row in rows):
        raise RuntimeError(
            "existing backend connections exceed requested pool size; recreate the idle router before comparing caps"
        )


def current_pool():
    rows = dict(line.split("|", 1) for line in admin("SHOW CONFIG").splitlines())
    return int(rows["default_pool_size"])


def pool_size(size):
    run(
        [sys.executable, "scripts/render-config.py"],
        env=dict(os.environ, POOL_SIZE=str(size)),
    )
    admin("RELOAD")
    if current_pool() != size:
        raise RuntimeError(
            "native PgDog configuration did not apply the requested pool size"
        )
    return {
        "at": now(),
        "size": size,
        "config": admin("SHOW CONFIG"),
        "pools": admin("SHOW POOLS"),
    }


def setup():
    validate_routing_config()
    customers = int(os.environ.get("CUSTOMERS", "10000"))
    if not 1 <= customers <= 1000000:
        raise ValueError("CUSTOMERS must be 1..1000000")
    members, physical = identities()
    if members:
        choices = list(members.values())
        if (
            any(value != choices[0] for value in choices)
            or choices[0]["customers"] != customers
        ):
            raise RuntimeError(
                "dataset identity/CUSTOMERS mismatch; preserve the current dataset"
            )
        identity = choices[0]["id"]
        folder = ROOT / str(uuid.UUID(identity))
        recorded = json.loads((folder / "fixture.json").read_text())
        if recorded["physical"] != physical:
            raise RuntimeError("physical databases no longer match their witness")
    else:
        for shard in range(1):
            if (
                sql(
                    primary(shard),
                    "SELECT to_regclass('public.orders') IS NOT NULL OR to_regclass('public.customers') IS NOT NULL",
                )
                != "f"
            ):
                raise RuntimeError(
                    "legacy tables lack a durability witness identity; preserve/diagnose them before a fresh lifecycle"
                )
        identity = str(uuid.uuid4())
        folder = ROOT / identity
        exclusive_save(
            folder / "fixture.json",
            {
                "id": identity,
                "customers": customers,
                "physical": physical,
                "created": now(),
            },
        )
    for shard in range(1):
        node = primary(shard)
        if node not in members:
            schema = Path("sql/schema.sql").read_text()
            sql(
                node,
                "BEGIN; CREATE TABLE lab_identity(slot int PRIMARY KEY CHECK(slot=1),fixture_id uuid NOT NULL,customers int NOT NULL); "
                + f"INSERT INTO lab_identity VALUES(1,'{identity}',{customers}); "
                + schema
                + "; COMMIT;",
            )
    # Apply any rendered setup changes before workloads. Live pool changes are
    # exercised separately through native RELOAD without restarting the router.
    compose("restart", "pgdog")
    compose("up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "pgdog")
    compose(
        "run",
        "--rm",
        "-T",
        "-e",
        "PGPASSWORD=local-admin",
        "client",
        "psql",
        "-h",
        "pgdog",
        "-p",
        "6432",
        "-U",
        "admin",
        "-d",
        "admin",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "RELOAD",
    )
    if not (folder / "routing.json").exists():
        # This example has one physical group, not native data sharding.
        exclusive_save(folder / "routing.json", [0] * customers)
    if not (folder / "seeded.json").exists():
        tool(folder, check_directory(folder, "seed"), "seed")
        verify(folder)
        exclusive_save(folder / "seeded.json", {"customers": customers, "at": now()})
    else:
        verify(folder)
    print("Dataset and retained witness:", folder, flush=True)


def native_state(node):
    return json.loads(
        sql(
            node,
            "SELECT json_build_object('system_id',(SELECT system_identifier::text FROM pg_control_system()),'replica',pg_is_in_recovery(),'started',pg_postmaster_start_time(),'lsn',CASE WHEN pg_is_in_recovery() THEN pg_last_wal_replay_lsn() ELSE pg_current_wal_flush_lsn() END)",
        )
    )


def wait_native(folder, deadline_seconds=180):
    recorded = json.loads((folder / "fixture.json").read_text())
    targets = {
        str(s): {
            "primary": (p := primary(s)),
            "lsn": sql(p, "SELECT pg_current_wal_flush_lsn()::text"),
        }
        for s in range(1)
    }
    deadline = time.monotonic() + deadline_seconds
    last = None
    while time.monotonic() < deadline:
        try:
            result = {}
            for shard in range(1):
                target = targets[str(shard)]
                if primary(shard) != target["primary"]:
                    raise RuntimeError(
                        "primary changed during the frozen native repair barrier"
                    )
                peers = json.loads(
                    sql(
                        target["primary"],
                        "SELECT coalesce(json_agg(json_build_object('node',application_name,'state',state,'sync',sync_state)),'[]') FROM pg_stat_replication",
                    )
                )
                expected = {f"s{shard}n{r}" for r in range(3)} - {target["primary"]}
                if {
                    p["node"] for p in peers if p["state"] == "streaming"
                } != expected or not any(p["sync"] == "sync" for p in peers):
                    raise RuntimeError(
                        "native streaming/synchronous redundancy incomplete"
                    )
                states = {}
                for replica in range(3):
                    node = f"s{shard}n{replica}"
                    state = native_state(node)
                    if state["system_id"] != recorded["physical"][str(shard)] or state[
                        "replica"
                    ] != (node != target["primary"]):
                        raise RuntimeError(
                            "wrong physical copy or primary role: " + node
                        )
                    if (
                        state["replica"]
                        and sql(
                            node,
                            f"SELECT coalesce(pg_last_wal_replay_lsn()>='{target['lsn']}'::pg_lsn,false)",
                        )
                        != "t"
                    ):
                        raise RuntimeError("replica below frozen WAL barrier: " + node)
                    states[node] = state
                result[str(shard)] = {
                    "frozen_lsn": target["lsn"],
                    "primary": target["primary"],
                    "peers": peers,
                    "members": states,
                }
            return result
        except RuntimeError as error:
            last = str(error)
            time.sleep(1)
    raise RuntimeError("native repair did not finish: " + str(last))


def verify(folder):
    native = wait_native(folder)
    reports = {}
    case = check_directory(folder, "router")
    tool(folder, case, "verify")
    reports["router"] = json.loads((case / "check.json").read_text())
    for shard in range(1):
        for replica in range(3):
            node = f"s{shard}n{replica}"
            case = check_directory(folder, node)
            tool(folder, case, "verify", node=node, shard=shard)
            reports[node] = json.loads((case / "check.json").read_text())
    report = {"at": now(), "native": native, "copies": reports}
    exclusive_save(
        folder / "checks" / ("full-" + str(time.time_ns()) + ".json"), report
    )
    print(
        "Verified router and all three physical copies:", reports["router"], flush=True
    )
    return report


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


def snapshot():
    result = {}
    for service in [
        *(f"etcd{i}" for i in range(3)),
        "pgdog",
        *(f"s{s}n{r}" for s in range(1) for r in range(3)),
    ]:
        info = owned(service)
        result[service] = {
            "id": info["Id"],
            "started": info["State"]["StartedAt"],
            "restarts": info["RestartCount"],
            "running": info["State"]["Running"],
            "paused": info["State"]["Paused"],
        }
        if re.fullmatch(r"s[012]n[012]", service):
            result[service]["postmaster_started"] = native_state(service)["started"]
    return result


def scenario(mode):
    validate_routing_config()
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
            "workers": int(os.environ.get("WORKERS", "8")),
            "pool_size": current_pool(),
            "strict_pool_cap": os.environ.get("REQUIRE_POOL_CAP") == "1",
            "offered_rows_per_second": int(os.environ.get("CHAOS_RATE", "25000"))
            if live
            else 0,
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
    original_pool = None
    proof = {}
    started = time.monotonic()
    try:
        proof["pools_before"] = pool_snapshot()
        if os.environ.get("REQUIRE_POOL_CAP") == "1":
            assert_pool_cap(proof["pools_before"], current_pool())
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
            proof["survivors_before"] = before
            shard = 0
            leader = primary(shard)
            if mode in ("failover", "router", "replica-crash"):
                target = leader
                if mode == "router":
                    target = "pgdog"
                elif mode == "replica-crash":
                    target = sql(
                        leader,
                        "SELECT application_name FROM pg_stat_replication WHERE sync_state='async'",
                    )
                    if not re.fullmatch("s0n[012]", target) or target == leader:
                        raise RuntimeError("no unique asynchronous replica")
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
                        raise RuntimeError(
                            "no different primary elected before deadline"
                        )
                    proof["promotion_seconds"] = time.monotonic() - began
                    proof["new_primary"] = new
                if mode != "router":
                    phase(case, "degraded", markers)
                    progress(case, writer, reader, markers[-1]["at"], shard=shard)
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
            elif mode == "pool-reload":
                original_pool = current_pool()
                proof["pool_reloads"] = []
                for size in (4, 16):
                    phase(case, "pool-" + str(size), markers)
                    proof["pool_reloads"].append(pool_size(size))
                    progress(case, writer, reader, markers[-1]["at"])
                    time.sleep(5)
                proof["restored_pool"] = pool_size(original_pool)
                original_pool = None
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
            proof["native_barrier_seconds"] = time.monotonic() - repair_started
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
        proof["pools_after"] = pool_snapshot()
        if os.environ.get("REQUIRE_POOL_CAP") == "1":
            assert_pool_cap(proof["pools_after"], current_pool())
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
        if original_pool is not None:
            try:
                pool_size(original_pool)
            except Exception as error:
                cleanup_errors.append(str(error))
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


def status():
    for shard in range(1):
        node = primary(shard)
        summary = sql(
            node,
            "SELECT json_build_object('customers',(SELECT count(*) FROM customers),'orders',(SELECT count(*) FROM orders),'replicas',(SELECT json_agg(json_build_object('node',application_name,'state',state,'sync',sync_state,'replay_lsn',replay_lsn)) FROM pg_stat_replication))",
        )
        print(f"shard={shard} primary={node} {summary}", flush=True)
    compose(
        "run",
        "--rm",
        "-T",
        "-e",
        "PGPASSWORD=local-admin",
        "client",
        "psql",
        "-h",
        "pgdog",
        "-p",
        "6432",
        "-U",
        "admin",
        "-d",
        "admin",
        "-v",
        "ON_ERROR_STOP=1",
        "-c",
        "SHOW REPLICATION",
        capture=False,
    )


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
            "router",
            "replica-crash",
            "pool-reload",
        ):
            scenario(mode)
        elif mode == "compare-pools":
            original = current_pool()
            os.environ["WORKERS"] = "64"
            prefix = os.environ.get("RUN_ID")
            try:
                for size in (4, 16):
                    pool_size(size)
                    # RELOAD preserves existing connections; it is not a hard
                    # shrink. Reopen the idle owned router for a real cap test.
                    owned("pgdog")
                    compose("restart", "pgdog")
                    compose(
                        "up",
                        "-d",
                        "--no-deps",
                        "--wait",
                        "--wait-timeout",
                        "120",
                        "pgdog",
                    )
                    if current_pool() != size:
                        raise RuntimeError("fresh router has wrong pool configuration")
                    os.environ["REQUIRE_POOL_CAP"] = "1"
                    if prefix:
                        os.environ["RUN_ID"] = prefix + "-pool" + str(size)
                    scenario("benchmark")
            finally:
                pool_size(original)
        elif mode == "replica-reads":
            folder, _ = fixture()
            verify(folder)
            value = compose(
                "run",
                "--rm",
                "-T",
                "client",
                "psql",
                "-X",
                "-qAt",
                "-h",
                "pgdog",
                "-p",
                "6432",
                "-U",
                "postgres",
                "-d",
                "orders",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "SET pgdog.role='replica'",
                "-c",
                "SELECT pg_is_in_recovery()",
            )
            if value.strip() != "t":
                raise RuntimeError("explicit replica routing not proven: " + value)
            rejected = []
            leader = primary(0)
            for node in ("s0n0", "s0n1", "s0n2"):
                if node == leader:
                    continue
                result = compose(
                    "exec",
                    "-T",
                    node,
                    "psql",
                    "-X",
                    "-U",
                    "postgres",
                    "-d",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-v",
                    "VERBOSITY=verbose",
                    "-c",
                    "BEGIN; INSERT INTO customers VALUES(-1,'must-reject'); ROLLBACK;",
                    check=False,
                )
                if result.returncode == 0 or "25006" not in result.stderr:
                    raise RuntimeError("native standby did not reject writes: " + node)
                rejected.append(
                    {"node": node, "sqlstate": "25006", "error": result.stderr}
                )
            case = check_directory(folder, "replica-reads")
            exclusive_save(
                case / "result.json",
                {
                    "at": now(),
                    "router_recovery_role": True,
                    "native_write_rejections": rejected,
                },
            )
        elif mode == "connection-burst":
            folder, _ = fixture()
            report = verify(folder)
            if report["copies"]["router"]["committed_without_ack"]:
                raise RuntimeError(
                    "connection-burst needs fully witnessed customer totals"
                )
            case = check_directory(folder, "connection-burst")
            env = dict(
                os.environ,
                WITNESS_DIR=str(folder.resolve()),
                RESULTS_DIR=str(case.resolve()),
            )
            compose(
                "run",
                "--rm",
                "-T",
                "-e",
                "DIRECT_DSN=postgres://postgres:local-postgres@"
                + primary(0)
                + ":5432/postgres?sslmode=disable",
                "workload",
                "burst",
                capture=False,
                env=env,
                timeout=180,
            )
            print("Connection burst evidence:", case, flush=True)
        elif mode == "pool-size":
            print(
                json.dumps(pool_size(int(os.environ.get("POOL_SIZE", "16"))), indent=2)
            )
        elif mode == "config":
            print(admin("SHOW CONFIG"), admin("SHOW POOLS"), admin("SHOW REPLICATION"))
        elif mode == "status":
            status()
        elif mode == "verify":
            folder, _ = fixture()
            verify(folder)
        elif mode == "down":
            folder, _ = fixture()
            verify(folder)
            for shard in range(1):
                sql(primary(shard), "DROP TABLE orders,customers,lab_identity")
            exclusive_save(folder / "closed.json", {"at": now()})
        else:
            raise ValueError(mode)


if __name__ == "__main__":
    main()
