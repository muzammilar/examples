"""Example-owned SQL recovery lab. Failed histories are never reset or reused."""

import argparse
import bisect
import csv
import fcntl
import hashlib
import json
import math
import os
import resource
from pathlib import Path
import shlex
import statistics
import subprocess
import time
import uuid
import zlib

from resources import Recorder, summarize

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
PROJECT = "cedardb-backup-recovery"
BACKENDS = ("cedar", "postgres")
ENV = os.environ | {
    "CEDAR_LAB_UID": str(os.getuid()),
    "CEDAR_LAB_GID": str(os.getgid()),
}


def run(args, *, env=None, timeout=300, input=None):
    return subprocess.run(
        args,
        cwd=ROOT,
        env=ENV | (env or {}),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=timeout,
        input=input,
    ).stdout


def compose(*args, env=None, timeout=300):
    return run(["docker", "compose", *args], env=env, timeout=timeout)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def inspect(identity):
    item = json.loads(run(["docker", "inspect", identity]))[0]
    labels = item["Config"].get("Labels") or {}
    if labels.get("com.docker.compose.project") != PROJECT or labels.get(
        "com.docker.compose.project.working_dir"
    ) != str(ROOT):
        raise RuntimeError("container ownership differs from this exact example")
    return item


def container(service):
    identities = compose("ps", "-a", "-q", service).split()
    if len(identities) != 1:
        raise RuntimeError(f"expected one {service} container")
    item = inspect(identities[0])
    if item["Config"]["Labels"].get("com.docker.compose.service") != service:
        raise RuntimeError("unexpected native service")
    return item


def owned_inventory():
    ids = run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"label=com.docker.compose.project={PROJECT}",
        ]
    ).split()
    return [inspect(identity) for identity in ids]


def app(backend, *args, timeout=300, reader=False):
    raw = compose(
        "run",
        "--rm",
        "--no-deps",
        "-T",
        "orders-workload",
        *args,
        env={
            "CEDAR_LAB_DB_HOST": backend,
            "CEDAR_LAB_ENGINE": "cedar" if backend.startswith("cedar") else "postgres",
            "CEDAR_LAB_USER": (
                "orders_owner" if backend.startswith("cedar") else "orders_reader"
            )
            if reader
            else "postgres",
            "CEDAR_LAB_PASSWORD": "Orders-Lab-1" if reader else "Cedar-Lab-1",
            "CEDAR_LAB_READ_ONLY": "1" if reader else "0",
        },
        timeout=timeout,
    )
    return [json.loads(line) for line in raw.splitlines()]


def maintenance(case, service, command, env=None):
    records = [
        json.loads(line)
        for line in compose(
            "run", "--rm", "--no-deps", "-T", service, command, env=env
        ).splitlines()
    ]
    outputs = [r["stdout"] for r in records if r.get("kind") == "maintenance-output"]
    if len(outputs) != 1 or not any(
        r.get("kind") == "process-resources" for r in records
    ):
        raise RuntimeError(
            "maintenance command did not return output and native resource evidence"
        )
    save(
        case.root / ("maintenance-" + uuid.uuid4().hex + ".json"),
        {
            "service": service,
            "command": command,
            "records": records,
            "captured_ns": time.time_ns(),
        },
    )
    return outputs[0]


def body(identity):
    prefix = f"customer-{identity % 10000:05d}|"
    return (prefix * 13)[:192] + hashlib.sha256(
        f"order:{identity}".encode()
    ).hexdigest()


def journal(path, active=False):
    if not path.exists():
        return []
    records = []
    for line in path.read_bytes().splitlines(keepends=True):
        if not line.endswith(b"\n"):
            if active:
                break
            raise RuntimeError("incomplete durable journal tail")
        records.append(json.loads(line))
    return records


def histories(backend, active=False):
    attempted, acknowledged = set(), set()
    generation = json.loads((RESULTS / "lab.json").read_text()).get(
        "generation", "initial"
    )
    for meta in (RESULTS / "histories").glob("*/meta.json"):
        info = json.loads(meta.read_text())
        if (
            info["backend"] != backend
            or info.get("generation", "initial") != generation
        ):
            continue
        for event in journal(meta.parent / "writer.jsonl", active=active):
            if event["kind"] == "intent":
                if event["id"] in attempted:
                    raise RuntimeError("historical operation ID was reused")
                attempted.add(event["id"])
            elif event["kind"] == "ok":
                acknowledged.add(event["id"])
    if not acknowledged <= attempted:
        raise RuntimeError("ACK without durable intent")
    return attempted, acknowledged


def check_read_history(writes, reads, seed_rows):
    acknowledgements = sorted(
        (e["ended_ns"], e["id"]) for e in writes if e["kind"] == "ok"
    )
    times, maxima = [], []
    highest = seed_rows - 1
    for ended, identity in acknowledgements:
        highest = max(highest, identity)
        times.append(ended)
        maxima.append(highest)
    checked = 0
    for event in reads:
        if event["kind"] != "ok":
            continue
        position = bisect.bisect_right(times, event["started_ns"]) - 1
        minimum = maxima[position] if position >= 0 else seed_rows - 1
        if event["id"] < minimum:
            raise RuntimeError(
                "successful latest-row read preceded an already acknowledged key"
            )
        checked += 1
    return {
        "successful_reads_checked": checked,
        "stale_successes": 0,
        "scope": "Offline lower-bound check: latest ID must cover ACKs completed before read invocation. Actors share the Docker VM wall clock. This is not a general linearizability or SQL anomaly checker.",
    }


def read_history(backend):
    lab = json.loads((RESULTS / "lab.json").read_text())
    writes, reads = [], []
    for path in (RESULTS / "histories").glob("*/meta.json"):
        meta = json.loads(path.read_text())
        if meta["backend"] == backend and meta.get("generation", "initial") == lab.get(
            "generation", "initial"
        ):
            writes.extend(journal(path.parent / "writer.jsonl"))
            reads.extend(journal(path.parent / "reader.jsonl"))
    return check_read_history(writes, reads, lab["rows"])


def verify_export(path, rows, attempted, acknowledged, required=None):
    required = acknowledged if required is None else required
    seen, next_seed, previous = set(), 0, -1
    with path.open(newline="") as stream:
        reader = csv.reader(stream)
        if next(reader) != ["id", "customer", "amount", "payload"]:
            raise RuntimeError("wrong export schema")
        for fields in reader:
            identity, customer, amount = map(int, fields[:3])
            if (
                len(fields) != 4
                or identity <= previous
                or customer != identity % 10000
                or amount != (identity % 100000) * 17 % 100000
                or fields[3] != body(identity)
            ):
                raise RuntimeError(f"wrong body/order at {identity}")
            previous = identity
            if identity < rows:
                if identity != next_seed:
                    raise RuntimeError("missing baseline row")
                next_seed += 1
            else:
                if identity not in attempted:
                    raise RuntimeError(f"unwitnessed live row {identity}")
                seen.add(identity)
    if next_seed != rows or not required <= seen:
        raise RuntimeError(
            f"missing baseline or acknowledged rows: {len(required - seen)} live"
        )
    digest, compressor, compressed = (
        hashlib.sha256(),
        zlib.compressobj(1, zlib.DEFLATED, 31),
        0,
    )
    compression_wall_ns, compression_cpu_ns = 0, 0
    with path.open("rb") as stream:
        while data := stream.read(1024 * 1024):
            digest.update(data)
            wall, cpu = time.perf_counter_ns(), time.process_time_ns()
            compressed += len(compressor.compress(data))
            compression_wall_ns += time.perf_counter_ns() - wall
            compression_cpu_ns += time.process_time_ns() - cpu
    wall, cpu = time.perf_counter_ns(), time.process_time_ns()
    compressed += len(compressor.flush())
    compression_wall_ns += time.perf_counter_ns() - wall
    compression_cpu_ns += time.process_time_ns() - cpu
    stat = path.stat()
    return {
        "seed_rows": rows,
        "live_rows": len(seen),
        "observed_live_ids": sorted(seen),
        "required_acks": len(required),
        "missing_required_acks": 0,
        "all_historical_acks": len(acknowledged),
        "snapshot_excluded_acks": len(acknowledged - seen),
        "unacknowledged_intents_present": len(seen - acknowledged),
        "csv_sha256": digest.hexdigest(),
        "csv_bytes": stat.st_size,
        "host_allocated_csv_bytes": stat.st_blocks * 512,
        "gzip1_bytes": compressed,
        "gzip1_ratio": stat.st_size / compressed,
        "compression_wall_seconds": compression_wall_ns / 1e9,
        "compression_cpu_seconds": compression_cpu_ns / 1e9,
        "compression_scope": "Off-path gzip level 1 calls only, excluding file reads/hash/fsync/body verification. Not native engine compression; native automatic codec CPU is not isolated from whole-container cost.",
    }


def reconcile(backend, target=None, required=None, active=False):
    config = json.loads((RESULTS / "lab.json").read_text())
    attempted, ack = histories(backend, active=active)
    folder = RESULTS / "exports"
    folder.mkdir(exist_ok=True)
    identity = uuid.uuid4().hex
    path = folder / (identity + ".csv")
    metrics = app(
        target or backend,
        "export",
        str(Path("/results/exports") / path.name),
        reader=target is not None,
    )
    result = verify_export(path, config["rows"], attempted, ack, required)
    if not active:
        result["read_history"] = read_history(backend)
    result["export_process"] = metrics
    save(folder / (identity + ".json"), result)
    path.unlink()  # Proof is fsynced first. Failed exports remain untouched.
    return result


def native_snapshot(backend):
    item = container(backend)
    if not item["State"]["Running"]:
        raise RuntimeError("native snapshot requested from stopped database")
    data = (
        "/var/lib/cedardb/data/database"
        if backend.startswith("cedar")
        else "/var/lib/postgresql/data"
    )
    raw = run(
        [
            "docker",
            "exec",
            item["Id"],
            "/bin/sh",
            "-c",
            f"for name in memory.peak cpu.stat io.stat memory.events; do echo __$name; cat /sys/fs/cgroup/$name; done; echo __DISK; du -sb {data}; du -s --block-size=1 {data}; echo __FILESYSTEM; df -PB1 {data}; echo __PROCESS; cat /proc/1/status",
        ]
    )
    if backend.startswith("cedar"):
        sql = "SELECT attributename,encoding,generalcompression,uncompressedsize,encodedsize,compressedsize FROM cedardb_compression_info WHERE tablename='orders'"
        scope = "Native per-column cold-data statistics only; empty rows do not establish absence of compression. Whole-database allocated bytes are separate."
    else:
        sql = "SELECT pg_column_compression(payload),count(*),SUM(pg_column_size(payload)) FROM orders GROUP BY pg_column_compression(payload)"
        scope = "Native payload storage/compression across all rows. NULL means no PostgreSQL column compression; tuple/index/WAL bytes are separate."
    try:
        compression = {
            "available": True,
            "scope": scope,
            "query": sql,
            "records": app(backend, "sql", sql),
        }
    except subprocess.CalledProcessError as error:
        compression = {
            "available": False,
            "scope": scope,
            "query": sql,
            "error": str(error),
            "stderr": error.stderr,
            "stdout": error.stdout,
        }
    return {
        "container": item,
        "native_counters_disk_and_main_process": raw,
        "native_compression": compression,
    }


def sample_disk(role, identity):
    if role not in (*BACKENDS, "cedar-restore", "postgres-restore"):
        return None
    state = inspect(identity)
    if not state["State"]["Running"]:
        raise RuntimeError("database stopped; live disk sampling unavailable")
    path = (
        "/var/lib/cedardb/data/database"
        if role.startswith("cedar")
        else "/var/lib/postgresql/data"
    )
    paths = [path] + (["/backups"] if role == "cedar" else [])
    command = "; ".join(
        f"du -sb {shlex.quote(p)}; du -s --block-size=1 {shlex.quote(p)}" for p in paths
    )
    lines = run(["docker", "exec", identity, "/bin/sh", "-ec", command]).splitlines()
    if len(lines) != len(paths) * 2:
        raise RuntimeError("incomplete native disk counters")
    return {
        "captured_ns": time.time_ns(),
        "paths": {
            p: {
                "logical_bytes": int(lines[2 * i].split()[0]),
                "allocated_bytes": int(lines[2 * i + 1].split()[0]),
            }
            for i, p in enumerate(paths)
        },
        "scope": "Database directories plus the shared backup volume counted once through cedar. CSV witnesses and backup restore copies are separate; a sampled maximum is not a native disk high-water counter.",
    }


def ensure_roles():
    found = app("cedar", "sql", "SELECT 1 FROM pg_user WHERE usename='orders_owner'")
    if not any(isinstance(row, list) for row in found):
        app("cedar", "sql", "CREATE USER orders_owner WITH PASSWORD 'Orders-Lab-1'")
    app("cedar", "sql", "ALTER TABLE orders OWNER TO orders_owner")
    for backend in ("postgres",):
        for role in ("orders_writer", "orders_reader"):
            found = app(backend, "sql", f"SELECT 1 FROM pg_user WHERE usename='{role}'")
            if not any(isinstance(row, list) for row in found):
                app(backend, "sql", f"CREATE USER {role} WITH PASSWORD 'Orders-Lab-1'")
            privilege = "SELECT, INSERT" if role == "orders_writer" else "SELECT"
            app(backend, "sql", f"GRANT {privilege} ON orders TO {role}")
    found = app(
        "postgres", "sql", "SELECT 1 FROM pg_roles WHERE rolname='backup_agent'"
    )
    if not any(isinstance(row, list) for row in found):
        app(
            "postgres",
            "sql",
            "CREATE ROLE backup_agent WITH LOGIN REPLICATION PASSWORD 'Backup-Lab-1'",
        )
    target = container("postgres")
    run(
        ["docker", "exec", "-i", target["Id"], "/bin/sh", "-eu"],
        input=(ROOT / "postgres-initdb/10-backup-auth.sh").read_text(),
    )
    app("postgres", "sql", "SELECT pg_reload_conf()")
    rules = app(
        "postgres",
        "sql",
        "SELECT count(*) FROM pg_hba_file_rules WHERE type='host' AND database=ARRAY['replication'] AND user_name=ARRAY['backup_agent'] AND auth_method='scram-sha-256' AND error IS NULL",
    )
    if [row for row in rules if isinstance(row, list)] != [[1]]:
        raise RuntimeError("dedicated replication authentication did not converge")


def phase_summary(events, start, end):
    chosen = [
        e for e in events if e["kind"] != "intent" and start <= e["started_ns"] < end
    ]
    groups = {}
    for kind in ("ok", "fail", "ambiguous", "mismatch"):
        times = sorted(e["elapsed_ns"] / 1e6 for e in chosen if e["kind"] == kind)
        groups[kind] = {
            "count": len(times),
            "rate": len(times) / ((end - start) / 1e9),
            "p50_ms": statistics.median(times) if times else None,
            "p99_ms": times[math.ceil(0.99 * len(times)) - 1] if times else None,
        }
    return groups


class LiveCase:
    def __init__(self, backend, action):
        self.backend, self.action = backend, action
        self.identity = uuid.uuid4().hex
        self.root = RESULTS / "histories" / self.identity
        self.root.mkdir(parents=True)
        self.actors, self.phases = {}, []
        self.cleaned = set()
        lab = json.loads((RESULTS / "lab.json").read_text())
        self.first = lab["next_id"]
        lab["next_id"] += 1000000
        save(RESULTS / "lab.json", lab)
        save(
            self.root / "meta.json",
            {
                "backend": backend,
                "action": action,
                "first_id": self.first,
                "rows": lab["rows"],
                "generation": lab.get("generation", "initial"),
                "rate_per_actor": int(ENV.get("RATE", "50")),
                "created_ns": time.time_ns(),
            },
        )

    def start(self):
        for role in ("writer", "reader"):
            name = f"cedar-recovery-{self.identity[:12]}-{role}"
            raw = compose(
                "run",
                "--detach",
                "--no-deps",
                "--name",
                name,
                "orders-workload",
                role,
                f"/results/histories/{self.identity}/{role}.jsonl",
                env={
                    "CEDAR_LAB_DB_HOST": self.backend,
                    "CEDAR_LAB_ENGINE": self.backend,
                    "FIRST_ID": str(self.first),
                    "CEDAR_LAB_USER": "orders_owner"
                    if self.backend == "cedar"
                    else "orders_" + role,
                    "CEDAR_LAB_READ_ONLY": "0",
                    "CEDAR_LAB_PASSWORD": "Orders-Lab-1",
                },
            )
            self.actors[role] = inspect(raw.strip())["Id"]

    def events(self, role):
        return journal(self.root / (role + ".jsonl"), active=True)

    def assert_running(self):
        for identity in self.actors.values():
            state = inspect(identity)["State"]
            if not state["Running"]:
                raise RuntimeError(f"live actor exited: {state}")

    def progress(self, before=None, timeout=30):
        before = before or {role: 0 for role in self.actors}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.assert_running()
            counts = {
                role: sum(e["kind"] == "ok" for e in self.events(role))
                for role in self.actors
            }
            if all(counts[role] >= before[role] + 5 for role in counts):
                return counts
            time.sleep(0.2)
        raise TimeoutError("independent read/write progress did not recover")

    def hold(self, recorder, name, seconds=5):
        recorder.phase = name
        started = time.time_ns()
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.assert_running()
            time.sleep(0.25)
        self.phases.append(
            {"name": name, "started_ns": started, "ended_ns": time.time_ns()}
        )
        save(self.root / "phases.json", self.phases)

    def close(self):
        errors = []
        for role, identity in self.actors.items():
            if identity in self.cleaned:
                continue
            item = inspect(identity)
            if item["State"]["Running"]:
                run(["docker", "stop", "--time", "10", identity])
            item = inspect(identity)
            save(self.root / (role + "-inspect.json"), item)
            logs = subprocess.run(
                ["docker", "logs", identity],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=30,
            ).stdout
            (self.root / (role + ".log")).write_text(logs)
            if item["State"]["Running"] or item["State"]["ExitCode"] != 0:
                errors.append(f"{role} failed or required forced termination")
                continue
            run(["docker", "rm", identity])
            self.cleaned.add(identity)
        if errors:
            raise RuntimeError("; ".join(errors))


def backup_restore(case, recorder):
    backend, identity = case.backend, case.identity
    _, required = histories(backend, active=True)
    save(case.root / "backup-required-acks.json", sorted(required))
    destination = f"/backups/{backend}/{identity}"
    maintenance(
        case,
        "backup-tools",
        f"test ! -L /backups/{backend}; mkdir -p /backups/{backend}; chown 999:999 /backups/{backend}",
    )
    free_space = maintenance(case, "backup-tools", "df -PB1 /backups")
    available = int(free_space.splitlines()[-1].split()[3])
    if available < 5 * 1024**3:
        raise RuntimeError(
            "less than 5 GiB free on backup storage; do not fill the shared Docker disk"
        )
    save(
        case.root / "backup-space.json",
        {
            "available_bytes": available,
            "minimum_free_bytes": 5 * 1024**3,
            "raw": free_space,
        },
    )
    recorder.phase = "backup"
    started = time.time_ns()
    if backend == "cedar":
        result = app(backend, "sql", f"BACKUP ALL TO '{destination}'")
    else:
        result = [
            json.loads(line)
            for line in compose(
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "postgres-backup",
                "pg-backup",
                destination,
            ).splitlines()
        ]
    case.phases.append(
        {"name": "backup", "started_ns": started, "ended_ns": time.time_ns()}
    )
    save(
        case.root / "native-backup.json", {"destination": destination, "result": result}
    )
    case.hold(recorder, "post-backup-tail")
    extra = {"CEDAR_LAB_RESTORE_ID": identity}
    target = f"/restore/{backend}" + ("/database" if backend == "cedar" else "")
    command = f"test -d {shlex.quote(destination)}; test ! -L {target}; "
    command += (
        f"test ! -e {target}; mkdir {target}; "
        if backend == "cedar"
        else f'test -z "$(ls -A {target})"; '
    )
    command += f"cp -a {shlex.quote(destination)}/. {target}/; chown -R 999:999 /restore/{backend}"
    maintenance(case, "restore-tools", command, env=extra)
    restored = backend + "-restore"
    compose("up", "--detach", "--wait", restored, env=extra)
    restored_state = container(restored)
    save(case.root / "restore-inspect.json", restored_state)
    recorder.targets.append((restored, restored_state["Id"]))
    denied = False
    try:
        app(
            restored,
            "sql",
            f"INSERT INTO orders VALUES ({case.first + 999999},0,0,'must-be-rejected')",
            reader=True,
        )
    except subprocess.CalledProcessError as error:
        save(
            case.root / "restore-write-rejected.json",
            {
                "returncode": error.returncode,
                "stdout": error.stdout,
                "stderr": error.stderr,
            },
        )
        denied = "42501" in error.stderr or "25006" in error.stderr
    if not denied:
        raise RuntimeError(
            "read-only restore account did not reject mutation with permission/read-only SQLSTATE 42501/25006"
        )
    verification = reconcile(backend, restored, required, active=True)
    save(case.root / "restored-data.json", verification)
    save(case.root / "restored-native.json", native_snapshot(restored))
    case.hold(recorder, "after-restore")
    # A failed restore retains its new volume. Only a fully verified copy is cleaned.
    compose("stop", restored, env=extra)
    stopped = container(restored)
    save(case.root / "restore-stopped.json", stopped)
    if stopped["State"]["Running"] or stopped["State"]["ExitCode"] != 0:
        raise RuntimeError("restore process was not cleanly fenced")
    compose("rm", "-f", restored, env=extra)
    for system in BACKENDS:
        name = f"{PROJECT}-restore-{identity}-{system}"
        volume = json.loads(run(["docker", "volume", "inspect", name]))[0]
        if volume.get("Labels", {}).get("com.docker.compose.project") != PROJECT:
            raise RuntimeError("restore volume ownership mismatch")
        run(["docker", "volume", "rm", name])


def exercise(backend, action):
    case = LiveCase(backend, action)
    before = {name: native_snapshot(name) for name in BACKENDS}
    save(case.root / "before.json", before)
    try:
        case.start()
        context = json.loads(run(["docker", "context", "inspect"]))[0]
        endpoint = (
            os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
        )
        targets = [(name, before[name]["container"]["Id"]) for name in BACKENDS] + list(
            case.actors.items()
        )
        with Recorder(
            case.root / "resources.jsonl", endpoint, targets, sample_disk
        ) as recorder:
            case.progress()
            case.hold(recorder, "baseline")
            progress = {
                role: sum(e["kind"] == "ok" for e in case.events(role))
                for role in case.actors
            }
            if action == "backup":
                backup_restore(case, recorder)
            else:
                original = container(backend)
                recorder.phase = "inject"
                if action == "crash":
                    run(["docker", "kill", "--signal", "KILL", original["Id"]])
                else:
                    run(["docker", "stop", "--time", "60", original["Id"]])
                stopped = inspect(original["Id"])
                save(case.root / "stopped.json", stopped)
                expected = 137 if action == "crash" else 0
                if (
                    stopped["State"]["Running"]
                    or stopped["State"]["ExitCode"] != expected
                ):
                    raise RuntimeError("native shutdown/fencing was not as expected")
                case.hold(recorder, "outage")
                recorder.phase = "restart"
                compose("up", "--detach", "--wait", backend)
                returned = container(backend)
                if returned["State"]["StartedAt"] == original["State"]["StartedAt"]:
                    raise RuntimeError("database did not restart")
                save(case.root / "restarted.json", returned)
            case.progress(progress)
            case.hold(recorder, "recovered")
            recorder.phase = "final-verification"
            case.close()
            verification = reconcile(backend)
            restore_summary = None
            if action == "backup":
                restored = json.loads((case.root / "restored-data.json").read_text())
                _, acknowledged = histories(backend)
                restored_ids = set(restored["observed_live_ids"])
                restore_summary = {
                    "final_source_acks": len(acknowledged),
                    "snapshot_excluded_acks_at_case_end": len(
                        acknowledged - restored_ids
                    ),
                    "required_pre_backup_acks": restored["required_acks"],
                    "missing_required_acks": restored["missing_required_acks"],
                    "scope": "Final source ACK population after live clone/read-only/full-body verification. Excluded later writes are snapshot RPO, not acknowledged loss from the running source.",
                }
            after = {name: native_snapshot(name) for name in BACKENDS}
            other = "postgres" if backend == "cedar" else "cedar"
            if (
                after[other]["container"]["State"]["StartedAt"]
                != before[other]["container"]["State"]["StartedAt"]
            ):
                raise RuntimeError("unrelated backend restarted")
            save(case.root / "after.json", after)
        phase_metrics = {
            role: {
                p["name"]: phase_summary(
                    journal(case.root / (role + ".jsonl")),
                    p["started_ns"],
                    p["ended_ns"],
                )
                for p in case.phases
            }
            for role in case.actors
        }
        for role, phases in phase_metrics.items():
            for name in ("baseline", "recovered"):
                metrics = phases[name]
                total = sum(item["count"] for item in metrics.values())
                if (
                    metrics["ok"]["count"] < 5
                    or metrics["ok"]["count"] / max(total, 1) < 0.95
                ):
                    raise RuntimeError(
                        f"{role} healthy phase {name} did not meet the 95% success gate"
                    )
            if action != "backup" and not sum(
                phases["outage"][kind]["count"] for kind in ("fail", "ambiguous")
            ):
                raise RuntimeError(f"{role} did not observe the stopped database")
        gaps = {}
        for role in case.actors:
            successes = [
                e for e in journal(case.root / (role + ".jsonl")) if e["kind"] == "ok"
            ]
            gaps[role] = max(
                (b["ended_ns"] - a["ended_ns"]) / 1e9
                for a, b in zip(successes, successes[1:])
            )
        save(
            case.root / "result.json",
            {
                "passed": True,
                "backend": backend,
                "action": action,
                "verification": verification,
                "restore_summary": restore_summary,
                "phases": phase_metrics,
                "longest_success_gap_seconds": gaps,
                "gap_scope": "Same-VM actor timestamps; includes requested pacing and client reconnect. Not a precise election or physical failure timestamp.",
                "resources": summarize(case.root / "resources.jsonl"),
            },
        )
        print("Passed", backend, action, case.identity, flush=True)
    except Exception as error:
        save(case.root / "failure.json", {"passed": False, "error": str(error)})
        # Restore service without touching data. Preserve failed containers/history.
        try:
            compose("up", "--detach", "--wait", backend)
            case.close()
        except Exception as cleanup_error:
            save(case.root / "cleanup-failure.json", {"error": str(cleanup_error)})
        raise


def verify_mutable_archives():
    """Destroy owned storage only after independent mutable witnesses are exported."""
    import mutable_oracle

    if not (RESULTS / "lab.json").exists():
        return
    generation = json.loads((RESULTS / "lab.json").read_text())["generation"]
    checked = []
    for path in sorted((RESULTS / "mutable").glob("*/config.json")):
        config = json.loads(path.read_text())
        if config["generation"] != generation:
            continue
        root = path.parent
        archive = root / "pre-teardown-archive.json"
        if not (root / "cleanup.json").exists() and not archive.exists():
            raise RuntimeError(
                "mutable state needs a verified archive before storage deletion: "
                + root.name
            )
        prefix = "pre-teardown-" if archive.exists() else ""
        proof = mutable_oracle.reconcile(
            root / (prefix + "rows.csv"),
            root / (prefix + "receipts.csv"),
            root / "writer.jsonl",
            config["rows"],
            config["pairs"],
        )
        if archive.exists() and proof != json.loads(archive.read_text())["proof"]:
            raise RuntimeError("mutable archive proof changed: " + root.name)
        checked.append(
            {
                "identity": root.name,
                "proof": proof,
                "scope": "Final data/receipt/writer-witness preservation only; historical reader formats and failed verdicts are not requalified",
                "failed_verdict_preserved": (root / "failure.json").exists()
                or (root / "invalidated.json").exists(),
            }
        )
    save(
        RESULTS / "generations" / generation / "mutable-teardown-audit.json",
        {"cases": checked},
    )


def setup():
    state = RESULTS / "lab.json"
    rows = int(ENV.get("ROWS", "1000000"))
    previous = json.loads(state.read_text()) if state.exists() else {}
    if previous and previous.get("phase", "ready") != "destroyed":
        if previous["rows"] != rows:
            raise RuntimeError("ROWS differs from retained fixture")
        if previous.get("phase") == "initializing":
            receipts = RESULTS / "generations" / previous["generation"]
            if not all(
                (receipts / (name + "-seed.json")).exists() for name in BACKENDS
            ):
                raise RuntimeError(
                    "incomplete seed: inspect preserved data/receipts; setup never overwrites it"
                )
        ensure_roles()
        for backend in BACKENDS:
            reconcile(backend)
        previous["phase"] = "ready"
        save(state, previous)
        return
    lab = {
        "rows": rows,
        "next_id": previous.get("next_id", 1000000000),
        "created_ns": time.time_ns(),
        "generation": uuid.uuid4().hex,
        "phase": "initializing",
    }
    save(state, lab)
    folder = RESULTS / "generations" / lab["generation"]
    folder.mkdir(parents=True, exist_ok=True)
    context = json.loads(run(["docker", "context", "inspect"]))[0]
    endpoint = os.environ.get("DOCKER_HOST") or context["Endpoints"]["docker"]["Host"]
    targets = [(name, container(name)["Id"]) for name in BACKENDS]
    with Recorder(
        folder / "setup-resources.jsonl", endpoint, targets, sample_disk
    ) as recorder:
        time.sleep(1)
        save(
            folder / "before-load.json",
            {name: native_snapshot(name) for name in BACKENDS},
        )
        for backend in BACKENDS:
            recorder.phase = backend + "-seed"
            metrics = app(backend, "seed")
            save(folder / (backend + "-seed.json"), {"rows": rows, "process": metrics})
        recorder.phase = "roles-and-full-verification"
        ensure_roles()
        for backend in BACKENDS:
            reconcile(backend)
        save(
            folder / "after-load.json",
            {name: native_snapshot(name) for name in BACKENDS},
        )
    save(
        folder / "setup-resource-summary.json",
        summarize(folder / "setup-resources.jsonl"),
    )
    lab["phase"] = "ready"
    save(state, lab)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "docker-up",
            "up",
            "test",
            "crash",
            "graceful",
            "backup",
            "test-production",
            "status",
            "down",
            "docker-down",
        ),
    )
    args = parser.parse_args()
    if RESULTS.is_symlink():
        raise RuntimeError("results must be an owned real directory")
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        owned_inventory()
        contract = json.loads((ROOT / "contract.json").read_text())
        if args.action == "docker-up":
            compose("build", "orders-workload", "postgres-backup")
            compose("up", "--detach", "--wait", *BACKENDS)
            header = {
                "created_ns": time.time_ns(),
                "contract": contract,
                "contract_sha256": hashlib.sha256(
                    (ROOT / "contract.json").read_bytes()
                ).hexdigest(),
                "docker_info": json.loads(
                    run(["docker", "info", "--format", "{{json .}}"])
                ),
                "host": list(os.uname()),
                "disk_class": "Docker Desktop virtual block storage; physical device cache/IOPS not independently qualified",
                "kubernetes": "not applicable",
                "topology": "one Docker VM; databases share host, each capped at 4 CPUs / 6 GiB from startup; each actor 1 CPU / 512 MiB",
                "images_and_limits": {name: container(name) for name in BACKENDS},
                "versions": {
                    name: app(name, "sql", "SELECT version()") for name in BACKENDS
                },
                "client_rate": int(ENV.get("RATE", "50")),
                "source_revision": run(["git", "rev-parse", "HEAD"]).strip(),
                "source_sha256": {
                    str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in ROOT.rglob("*")
                    if p.is_file()
                    and not any(
                        x in {"results", "__pycache__", ".build"}
                        for x in p.relative_to(ROOT).parts
                    )
                },
            }
            save(RESULTS / ("environment-" + uuid.uuid4().hex + ".json"), header)
            print(
                "Environment/contract recorded; pinned images and startup limits verified.",
                flush=True,
            )
        elif args.action == "up":
            setup()
        elif args.action == "test":
            for backend in BACKENDS:
                print(backend, json.dumps(reconcile(backend)))
        elif args.action in ("crash", "graceful", "backup", "test-production"):
            actions = (
                ("graceful", "crash", "backup")
                if args.action == "test-production"
                else (args.action,)
            )
            for action in actions:
                for backend in BACKENDS:
                    exercise(backend, action)
        elif args.action == "status":
            print(compose("ps", "-a"))
        elif args.action == "down":
            compose("--profile", "restore", "stop")
            stopped = owned_inventory()
            save(RESULTS / ("stopped-" + uuid.uuid4().hex + ".json"), stopped)
            if any(
                item["State"]["Running"] or item["State"]["ExitCode"] != 0
                for item in stopped
            ):
                raise RuntimeError(
                    "owned process did not stop cleanly; preserve state/diagnostics"
                )
        else:
            volumes = run(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={PROJECT}",
                ]
            ).split()
            for name in volumes:
                volume = json.loads(run(["docker", "volume", "inspect", name]))[0]
                if (
                    volume.get("Labels", {}).get("com.docker.compose.project")
                    != PROJECT
                ):
                    raise RuntimeError("volume owner changed")
            unresolved = [
                p
                for p in (RESULTS / "histories").glob("*/failure.json")
                if not (p.parent / "data-reconciled.json").exists()
            ]
            if unresolved:
                raise RuntimeError(
                    "failed histories require successful retained-data reconciliation before deleting database storage"
                )
            verify_mutable_archives()
            compose("--profile", "restore", "--profile", "tools", "down", "--volumes")
            # UUID restore volumes from failed cases remain explicit evidence.
            remaining = run(
                [
                    "docker",
                    "volume",
                    "ls",
                    "-q",
                    "--filter",
                    f"label=com.docker.compose.project={PROJECT}",
                ]
            ).split()
            for tag in ("cedardb-recovery-orders:dev", "cedardb-recovery-backup:dev"):
                image_ids = run(
                    ["docker", "image", "ls", "-q", "--filter", "reference=" + tag]
                ).split()
                if image_ids:
                    image = json.loads(run(["docker", "image", "inspect", tag]))[0]
                    if (
                        image.get("Config", {})
                        .get("Labels", {})
                        .get("org.examples.lab")
                        != PROJECT
                    ):
                        raise RuntimeError("built image ownership mismatch")
                    run(["docker", "image", "rm", tag])
            state = RESULTS / "lab.json"
            if state.exists():
                lab = json.loads(state.read_text())
                lab["phase"] = "destroyed"
                save(state, lab)
            save(
                RESULTS / "teardown.json",
                {
                    "deleted_main_resources": True,
                    "preserved_histories": True,
                    "retained_restore_volumes": remaining,
                },
            )


if __name__ == "__main__":
    started = time.monotonic()
    before = resource.getrusage(resource.RUSAGE_SELF)
    children_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        report = {
            "passed": False,
            "error": str(error),
            "error_type": type(error).__name__,
            "at_ns": time.time_ns(),
        }
        if isinstance(error, subprocess.CalledProcessError):
            report.update(stdout=error.stdout, stderr=error.stderr)
        if RESULTS.is_dir() and not RESULTS.is_symlink():
            save(RESULTS / ("controller-failure-" + uuid.uuid4().hex + ".json"), report)
        raise
    finally:
        if RESULTS.is_dir() and not RESULTS.is_symlink():
            after = resource.getrusage(resource.RUSAGE_SELF)
            children_after = resource.getrusage(resource.RUSAGE_CHILDREN)
            save(
                RESULTS / "controller-costs" / (uuid.uuid4().hex + ".json"),
                {
                    "action": os.sys.argv[1:],
                    "ended_ns": time.time_ns(),
                    "wall_seconds": time.monotonic() - started,
                    "user_cpu_seconds": after.ru_utime - before.ru_utime,
                    "system_cpu_seconds": after.ru_stime - before.ru_stime,
                    "cli_children_user_cpu_seconds": children_after.ru_utime
                    - children_before.ru_utime,
                    "cli_children_system_cpu_seconds": children_after.ru_stime
                    - children_before.ru_stime,
                    "process_lifetime_peak_rss_bytes": after.ru_maxrss
                    * (1 if os.uname().sysname == "Darwin" else 1024),
                    "scope": "Host controller/oracle/compression and child Docker CLI processes. Separate from VM database/workload cgroups. Compression timers overlap this total; do not sum twice. Docker daemon/hypervisor overhead and controller average RSS are unavailable.",
                },
            )
