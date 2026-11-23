"""Independent native IPC writer/reader, external witness, retained-journal crashes."""

import csv
import fcntl
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import threading
import time
import uuid

import experiment as bench
import ingestion
from journal import NativeIPC, inspect_owned


def exact_row_query(row):
    predicates = [
        f"(== {column} {value})"
        for column, value in zip(
            ("id", "customer", "symbol", "day", "qty", "price"), row, strict=True
        )
    ]
    predicate = predicates.pop()
    for term in reversed(predicates):
        predicate = f"(and {term} {predicate})"
    return f"(count (select {{from: trades where: {predicate}}}))"


class Witness:
    def __init__(self, path):
        self.stream = path.open("x")
        self.lock = threading.Lock()
        self.events = []

    def add(self, **event):
        event.update(monotonic_ns=time.monotonic_ns(), utc_ns=time.time_ns())
        with self.lock:
            self.stream.write(json.dumps(event, separators=(",", ":")) + "\n")
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.events.append(event)
        return event

    def close(self):
        self.stream.close()


def reconcile(seed, exported, events):
    intents, completed, acknowledged = {}, set(), set()
    for event in events:
        if event["kind"] == "intent":
            if event["id"] in intents:
                raise RuntimeError("witness reused an operation ID")
            intents[event["id"]] = event["row"]
        elif event["kind"] == "write":
            identity = event["id"]
            if identity not in intents or identity in completed:
                raise RuntimeError("write has no unique preceding intent")
            completed.add(identity)
            if event["status"] == "ok":
                acknowledged.add(identity)
    seen = set()
    with seed.open() as expected, exported.open() as actual:
        source, output = csv.reader(expected), csv.reader(actual)
        if next(source) != next(output):
            raise RuntimeError("export schema differs")
        seed_rows = 0
        for row in source:
            if next(output, None) != row:
                raise RuntimeError("seed body lost, duplicated or changed at " + row[0])
            seed_rows += 1
        for raw in output:
            row = list(map(int, raw))
            identity = row[0]
            if identity in seen or intents.get(identity) != row:
                raise RuntimeError(
                    "unexpected, duplicate or corrupt live body: " + str(row)
                )
            seen.add(identity)
    missing = acknowledged - seen
    if missing:
        raise RuntimeError("acknowledged bodies missing: " + str(sorted(missing)[:10]))
    with exported.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "seed_rows_verified": seed_rows,
        "intents": len(intents),
        "acknowledged": len(acknowledged),
        "live_rows_verified": len(seen),
        "applied_unacknowledged_intents": len(seen - acknowledged),
        "acknowledged_loss": 0,
        "wrong_or_duplicate_bodies": 0,
        "export_sha256": digest,
    }


def summarize(events):
    grouped = {}
    for event in events:
        if event["kind"] not in {"read", "write"}:
            continue
        key = "/".join((event["phase"], event["kind"], event["status"]))
        grouped.setdefault(key, []).append(event["elapsed_ns"] / 1e6)
    result = {}
    for key, values in grouped.items():
        values.sort()
        result[key] = {
            "count": len(values),
            "mean_ms": sum(values) / len(values),
            "p50_ms": values[math.ceil(len(values) * 0.5) - 1],
            "p99_ms": values[math.ceil(len(values) * 0.99) - 1],
            "percentile_scope": "Nearest rank; low-count tails are effectively maxima, not established tail behavior",
        }
    return result


def recovery_observations(events):
    observations = {}
    for cycle in (1, 2):
        start = next(
            e["monotonic_ns"]
            for e in events
            if e["kind"] == "phase" and e["phase"] == f"recovery-{cycle}"
        )
        for kind in ("read", "write"):
            successful = [
                e for e in events if e["kind"] == kind and e["status"] == "ok"
            ]
            before = max(
                e["monotonic_ns"] for e in successful if e["monotonic_ns"] < start
            )
            after = min(
                e["monotonic_ns"] for e in successful if e["monotonic_ns"] >= start
            )
            observations[f"cycle-{cycle}/{kind}"] = {
                "first_success_after_start_request_ms": (after - start) / 1e6,
                "observed_success_gap_ms": (after - before) / 1e6,
                "scope": "External fsynced completion timestamps; gap includes deliberate outage and client pacing/deadlines. Startup request includes Docker start plus replay; not isolated engine replay or election time.",
            }
    return observations


def run():
    started = time.monotonic_ns()
    host_before = resource.getrusage(resource.RUSAGE_SELF)
    scenario = os.environ.get("JOURNAL_SCENARIO", "process-crash")
    if scenario not in {"process-crash", "writer-lock"}:
        raise ValueError("JOURNAL_SCENARIO must be process-crash or writer-lock")
    storage = os.environ.get("JOURNAL_STORAGE", "volume")
    if storage not in {"bind", "volume"}:
        raise ValueError("JOURNAL_STORAGE must be bind or volume")
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        existing = bench.capture(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=rayforce-duckdb-journal",
            ],
            text=True,
        ).strip()
        if existing:
            raise RuntimeError(
                "journal project already has containers; preserve and inspect them before retrying"
            )
        fixture = bench.prepare()
        root = bench.RESULTS / "journal-live" / uuid.uuid4().hex
        state = root / "state"
        state.mkdir(parents=True)
        env = dict(
            os.environ,
            JOURNAL_STORAGE_PATH=str(state),
            LAB_UID=str(os.getuid()),
            LAB_GID=str(os.getgid()),
        )
        compose = [
            "docker",
            "compose",
            "-f",
            str(bench.ROOT / "deploy/compose/docker-compose.journal.yml"),
        ]
        if storage == "volume":
            env["JOURNAL_VOLUME"] = "rayforce-journal-data-" + root.name
            compose += ["-f", str(bench.ROOT / "deploy/compose/docker-compose.journal-volume.yml")]
        compose += ["--profile", "tools"]
        baseline = bench.integer("JOURNAL_BASELINE_S", 10, 5, 300)
        outage = bench.integer("JOURNAL_OUTAGE_S", 5, 4, 60)
        recovery = bench.integer("JOURNAL_RECOVERY_S", 15, 10, 300)
        witness = Witness(root / "history.jsonl")
        ingestion.save(
            root / "setup.json",
            {
                "fixture": fixture,
                "scenario": scenario,
                "storage": storage,
                "volume_name": env.get("JOURNAL_VOLUME"),
                "scope": "One-host synchronous Rayforce IPC journal correctness; offered load is not capacity",
                "timings_s": {
                    "baseline": baseline,
                    "outage": outage,
                    "recovery": recovery,
                },
                "offered_rates_per_second": {"write": 50, "read": 20},
                "mutation_retries": 0,
                "native_connect_timeout_ms": 3000,
                "native_round_trip_timeout_ms": 3000,
                "docker_info": json.loads(
                    bench.capture(
                        ["docker", "info", "--format", "{{json .}}"], text=True
                    )
                ),
                "host": list(os.uname()),
                "cpu_model": bench.capture(
                    ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], text=True
                ).strip()
                if os.uname().sysname == "Darwin"
                else "See native host CPU inventory; not resolved by this controller",
                "source_sha256": {
                    str(p.relative_to(bench.ROOT)): hashlib.sha256(
                        p.read_bytes()
                    ).hexdigest()
                    for p in [
                        Path(__file__),
                        bench.ROOT / "scripts/experiment.py",
                        bench.ROOT / "projects/ingestion/scripts/ingestion.py",
                        bench.ROOT / "projects/rayforce-journal/scripts/journal.py",
                        bench.ROOT / "projects/rayforce-journal/scripts/journal_fencing.py",
                        bench.ROOT / "projects/rayforce-journal/scripts/journal_volume.py",
                        bench.ROOT / "deploy/compose/docker-compose.journal-volume.yml",
                        bench.ROOT / "config/persistence-contract.json",
                        bench.ROOT / "analytics-workload/cpp/ipc.cpp",
                        bench.ROOT / "deploy/compose/docker-compose.journal.yml",
                        bench.ROOT / "docker/Dockerfile",
                    ]
                },
                "limits": "No HA, physical power-loss or OS-cold claim. 1 CPU/4GiB engine; 1 CPU/512MiB per native IPC actor, swap disabled; external fsynced host witness/controller measured separately. Bind-mount I/O can bypass cgroup block counters.",
            },
        )
        actors, threads, errors, resources = [], [], [], []
        stop = threading.Event()
        collector_stop = threading.Event()
        phase = "bootstrap"
        acknowledged = []
        names = {}
        actor_starts = {}
        server = None
        result = None
        volume_exported = False
        resource_log = (root / "resources.jsonl").open("x")

        def sample():
            endpoint = bench.docker_socket()
            while not collector_stop.is_set():
                try:
                    snapshot = {
                        "phase": phase,
                        "monotonic_ns": time.monotonic_ns(),
                        "roles": {},
                        "disk": bench.storage_sample(state)
                        if storage == "bind"
                        else {
                            "files": {},
                            "available": False,
                            "reason": "native server unavailable for volume observation",
                        },
                    }
                    if storage == "volume" and server is not None:
                        from journal_volume import sample as volume_sample

                        native_before = inspect_owned(server)
                        if native_before["State"]["Running"]:
                            try:
                                snapshot["disk"] = volume_sample(server)
                            except subprocess.CalledProcessError:
                                native_after = inspect_owned(server)
                                if (
                                    native_after["State"]["Running"]
                                    and native_after["State"]["StartedAt"]
                                    == native_before["State"]["StartedAt"]
                                ):
                                    raise
                                snapshot["disk"] = {
                                    "files": {},
                                    "available": False,
                                    "reason": "native generation stopped/restarted during disk sampling",
                                }
                    for role, identifier in list(names.items()):
                        info = inspect_owned(identifier)
                        if info["State"]["Running"]:
                            snapshot["roles"][role] = {
                                "started_at": info["State"]["StartedAt"],
                                "sample": bench.resource_sample(endpoint, identifier),
                            }
                        else:
                            snapshot["roles"][role] = {
                                "running": False,
                                "exit_code": info["State"]["ExitCode"],
                            }
                    resources.append(snapshot)
                    resource_log.write(
                        json.dumps(snapshot, separators=(",", ":")) + "\n"
                    )
                    resource_log.flush()
                except Exception as error:
                    errors.append(error)
                    stop.set()
                    return
                collector_stop.wait(0.2)

        def control(query, expected=None, ready=False):
            deadline = time.monotonic() + 30
            while True:
                reply = actors[0].query(query)
                witness.add(kind="control", phase=phase, source=query, **reply)
                if reply["status"] == "ok":
                    if expected is not None and reply["body"] != expected:
                        raise RuntimeError("control result differs: " + str(reply))
                    return reply
                if not ready or time.monotonic() > deadline:
                    raise RuntimeError("control failed: " + str(reply))
                time.sleep(0.1)

        def checkpoint(label):
            inspections = {}
            for role, identifier in names.items():
                info = inspect_owned(identifier)
                inspections[role] = info
                if role != "server" and (
                    not info["State"]["Running"]
                    or info["State"]["StartedAt"] != actor_starts[role]
                ):
                    raise RuntimeError("independent actor exited/restarted: " + role)
                if info["State"]["Running"]:
                    raw = bench.capture(
                        [
                            "docker",
                            "exec",
                            identifier,
                            "sh",
                            "-c",
                            "cat /proc/1/status; echo __CPU__; cat /sys/fs/cgroup/cpu.stat; echo __MEMORY__; cat /sys/fs/cgroup/memory.peak; cat /sys/fs/cgroup/memory.events; echo __IO__; cat /sys/fs/cgroup/io.stat",
                        ],
                        text=True,
                    )
                    (root / f"{label}-{role}-native.txt").write_text(raw)
            ingestion.save(root / (label + "-inspect.json"), inspections)

        def hold(seconds):
            until = time.monotonic() + seconds
            while time.monotonic() < until:
                if errors:
                    raise RuntimeError(
                        "background workload/collector failed"
                    ) from errors[0]
                time.sleep(0.05)

        try:
            if storage == "volume":
                from journal_volume import initialize

                initialize(compose, env, root)
            collector = threading.Thread(target=sample, name="resources")
            collector.start()
            subprocess.run(
                compose + ["up", "-d", "rayforce"], env=env, check=True, timeout=60
            )
            server = subprocess.check_output(
                compose + ["ps", "-q", "rayforce"], env=env, text=True, timeout=30
            ).strip()
            info = inspect_owned(server)
            if not any(
                m["Destination"] == "/state"
                and m["RW"]
                and (
                    m.get("Name") == env["JOURNAL_VOLUME"]
                    if storage == "volume"
                    else m["Source"] == str(state)
                )
                for m in info["Mounts"]
            ):
                raise RuntimeError("server storage is not this exact owned case")
            if (
                "RAYFORCE_NO_FSYNC=0" not in info["Config"]["Env"]
                or info["HostConfig"]["MemorySwap"] != 4 * 1024**3
            ):
                raise RuntimeError("native durability/resource settings differ")
            names["server"] = server
            for role in ("control", "writer", "reader"):
                name = "rayforce-journal-" + role + "-" + root.name[:8]
                actors.append(NativeIPC(compose, env, name, root / (role + ".stderr")))
                # Wait for native readiness before depending on its container identity.
                if role == "control":
                    control("(+ 1 1)", "2", ready=True)
                else:
                    response = actors[-1].query("(+ 1 1)")
                    if response["status"] != "ok" or response["body"] != "2":
                        raise RuntimeError("independent actor failed to connect")
                actor_info = inspect_owned(name)
                names[role] = actor_info["Id"]
                actor_starts[role] = actor_info["State"]["StartedAt"]
            control("(get (.log.status) 'open)", "true")
            control("(get (.log.status) 'mode)", "sync")
            for query in bench.queries("rayforce")[0]:
                control(query)
            control("(count trades)", str(fixture["rows"]))
            control("(.log.snapshot)")
            with (bench.RESULTS / "data/trades.csv").open() as source:
                first = list(map(int, next(csv.DictReader(source)).values()))
            control(exact_row_query(first), "1")

            def writer():
                try:
                    identity = fixture["rows"]
                    while not stop.is_set():
                        before = time.monotonic()
                        row = ingestion.trade(identity)
                        intent = witness.add(
                            kind="intent", phase=phase, id=identity, row=row
                        )
                        reply = actors[1].query(ingestion.insert("rayforce", [row]))
                        witness.add(
                            kind="write",
                            phase=intent["phase"],
                            end_phase=phase,
                            id=identity,
                            invocation_ns=intent["monotonic_ns"],
                            **reply,
                        )
                        if reply["status"] == "ok":
                            acknowledged.append(row)
                        identity += 1
                        stop.wait(max(0, 0.02 - (time.monotonic() - before)))
                except Exception as error:
                    errors.append(error)
                    stop.set()

            def reader():
                try:
                    sequence = 0
                    while not stop.is_set():
                        before = time.monotonic()
                        row = (
                            acknowledged[-1] if sequence % 2 and acknowledged else first
                        )
                        beginning = time.monotonic_ns()
                        initial_phase = phase
                        witness.add(
                            kind="read-intent",
                            phase=initial_phase,
                            id=row[0],
                            sequence=sequence,
                        )
                        reply = actors[2].query(exact_row_query(row))
                        if reply["status"] == "ok" and reply["body"] != "1":
                            reply["status"] = "mismatch"
                        witness.add(
                            kind="read",
                            phase=initial_phase,
                            end_phase=phase,
                            id=row[0],
                            sequence=sequence,
                            invocation_ns=beginning,
                            temperature="recent_ack"
                            if row is not first
                            else "historical_resident",
                            **reply,
                        )
                        if reply["status"] == "mismatch":
                            raise RuntimeError(
                                "independent reader observed wrong acknowledged body"
                            )
                        sequence += 1
                        stop.wait(max(0, 0.05 - (time.monotonic() - before)))
                except Exception as error:
                    errors.append(error)
                    stop.set()

            phase = "baseline"
            witness.add(kind="phase", phase=phase)
            for function in (writer, reader):
                thread = threading.Thread(target=function, name=function.__name__)
                thread.start()
                threads.append(thread)
            hold(baseline)
            if scenario == "writer-lock":
                from journal_fencing import probe

                for kind in ("read", "write"):
                    if not any(
                        e["kind"] == kind and e.get("status") == "ok"
                        for e in witness.events
                    ):
                        raise RuntimeError("no live progress before contender")
                checkpoint("writer-lock-before")
                phase = "writer-lock"
                witness.add(kind="phase", phase=phase)
                probe(compose, env, state, root, server, witness)
                checkpoint("writer-lock-after")
                phase = "after-lock"
                witness.add(kind="phase", phase=phase)
                hold(baseline)
            else:
                for cycle in (1, 2):
                    if not all(
                        any(
                            e["kind"] == kind
                            and e.get("status") == "ok"
                            and e["phase"] == phase
                            for e in witness.events
                        )
                        for kind in ("read", "write")
                    ):
                        raise RuntimeError(
                            "no independent read/write progress before fault"
                        )
                    checkpoint(f"cycle-{cycle}-before")
                    phase = f"outage-{cycle}"
                    witness.add(kind="phase", phase=phase)
                    info = inspect_owned(server)
                    subprocess.run(
                        ["docker", "kill", "--signal", "KILL", server],
                        check=True,
                        timeout=30,
                    )
                    dead = inspect_owned(server)
                    ingestion.save(root / f"cycle-{cycle}-killed.json", dead)
                    if dead["State"]["Running"] or dead["State"]["ExitCode"] != 137:
                        raise RuntimeError("server kill was not proven")
                    hold(outage)
                    phase = f"recovery-{cycle}"
                    witness.add(kind="phase", phase=phase)
                    subprocess.run(
                        compose + ["start", "rayforce"], env=env, check=True, timeout=30
                    )
                    control("(+ 1 1)", "2", ready=True)
                    if (
                        inspect_owned(server)["State"]["StartedAt"]
                        == info["State"]["StartedAt"]
                    ):
                        raise RuntimeError("server generation did not change")
                    control("(get (.log.status) 'open)", "true")
                    control("(get (.log.status) 'mode)", "sync")
                    hold(recovery)
                    checkpoint(f"cycle-{cycle}-recovered")
                    if cycle == 1:
                        # Snapshot plus a new tail is exercised by the second crash.
                        control("(.log.snapshot)")
                        phase = "second-snapshot-tail"
                        witness.add(kind="phase", phase=phase)
                        hold(baseline)
            stop.set()
            for thread in threads:
                thread.join(timeout=12)
                if thread.is_alive():
                    raise RuntimeError("live worker failed to stop")
            if errors:
                raise RuntimeError("background failure") from errors[0]
            for kind in ("read", "write"):
                for completed_phase in (
                    ("baseline", "writer-lock", "after-lock")
                    if scenario == "writer-lock"
                    else (
                        "baseline",
                        "recovery-1",
                        "second-snapshot-tail",
                        "recovery-2",
                    )
                ):
                    if not any(
                        e["kind"] == kind
                        and e.get("status") == "ok"
                        and e["phase"] == completed_phase
                        for e in witness.events
                    ):
                        raise RuntimeError(
                            "missing live progress: " + kind + "/" + completed_phase
                        )
            if scenario == "writer-lock" and any(
                e["kind"] in {"read", "write"} and e["status"] != "ok"
                for e in witness.events
            ):
                raise RuntimeError(
                    "healthy primary returned errors during contender rejection"
                )
            phase = "reconciliation"
            witness.add(kind="phase", phase=phase)
            control(
                '(.csv.write (select {from: trades asc: id}) "/state/recovered.csv")'
            )
            control(
                '(.csv.write (select {from: customers asc: customer}) "/state/customers.csv")'
            )
            if storage == "volume":
                from journal_volume import export

                checkpoint("final")
                collector_stop.set()
                collector.join(timeout=30)
                if collector.is_alive():
                    raise RuntimeError("collector did not stop before state export")
                # Workload has ended. Fence the sole owner before exporting evidence.
                export(server, state, root)
                volume_exported = True
            # Re-read the fsynced external artifact instead of trusting in-memory counters.
            events = [
                json.loads(line)
                for line in (root / "history.jsonl").read_text().splitlines()
            ]
            proof = reconcile(
                bench.RESULTS / "data/trades.csv",
                state / "recovered.csv",
                events,
            )
            dimension = bench.RESULTS / "data/customers.csv"
            proof["customers"] = ingestion.verify_export(
                state / "customers.csv",
                {
                    "rows": 10000,
                    "bytes": dimension.stat().st_size,
                    "sha256": fixture["sha256"]["customers.csv"],
                },
            )
            if storage == "bind":
                checkpoint("final")
            result = {
                "passed": False,
                "scenario_passed": True,
                "proof": proof,
                "phases": summarize(events),
                "recovery_observations": recovery_observations(events)
                if scenario == "process-crash"
                else {},
                "wall_ns": time.monotonic_ns() - started,
                "scope": "Ten rejected cross-container journal opens under independent live writers/readers, unchanged authoritative process and complete body witness; not cross-host lease fencing or capacity"
                if scenario == "writer-lock"
                else "Two retained-volume SIGKILL/replay cycles with live independent writers/readers, second snapshot plus tail; not capacity, HA, async journal, torn-tail or power-loss qualification",
            }
            ingestion.save(root / "result.json", result)
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=12)
            collector_stop.set()
            if "collector" in locals():
                collector.join(timeout=30)
                if collector.is_alive():
                    errors.append(RuntimeError("resource collector did not stop"))
            resource_log.close()
            witness.close()
            cleanup_errors = []
            for actor in actors:
                try:
                    actor.close()
                except Exception as error:
                    cleanup_errors.append(str(error))
            try:
                with (root / "server.log").open("w") as log:
                    subprocess.run(
                        compose + ["logs", "--no-color"],
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=True,
                        timeout=30,
                    )
            finally:
                if storage == "volume" and server is not None and not volume_exported:
                    try:
                        from journal_volume import export

                        export(server, state, root)
                        volume_exported = True
                    except Exception as error:
                        cleanup_errors.append(
                            "Native volume preserved; export failed: " + str(error)
                        )
                teardown = ["down", "--remove-orphans"]
                if storage == "volume" and volume_exported:
                    teardown += ["--volumes"]
                subprocess.run(compose + teardown, env=env, check=True, timeout=60)
            # Native files are now stable. Gzip is a separate off-path layer.
            compression = []
            compression_start = time.monotonic_ns()
            compression_cpu = time.process_time_ns()
            for path in sorted(state.glob("journal*")):
                raw = path.read_bytes()
                compression.append(
                    {
                        "file": path.name,
                        "bytes": len(raw),
                        "gzip_level_1_bytes": len(
                            gzip.compress(raw, compresslevel=1, mtime=0)
                        ),
                        "allocated_bytes": path.stat().st_blocks * 512,
                    }
                )
            usage = resource.getrusage(resource.RUSAGE_SELF)
            ingestion.save(
                root / "costs.json",
                {
                    "native_files_off_path_gzip": compression,
                    "compression_wall_ns": time.monotonic_ns() - compression_start,
                    "compression_cpu_ns": time.process_time_ns() - compression_cpu,
                    "host_cpu_core_seconds": usage.ru_utime
                    + usage.ru_stime
                    - host_before.ru_utime
                    - host_before.ru_stime,
                    "host_peak_rss_bytes": usage.ru_maxrss
                    * (1 if os.uname().sysname == "Darwin" else 1024),
                    "resources_samples": len(resources),
                    "sampling_scope": "200ms plus serial Docker API/inspection overhead; generation-keyed CPU counters, charged memory and non-atomic directory samples, native cgroup CPU/memory/IO and PID1 RSS at checkpoints. Exact raw cadence retained; physical bind-mount I/O/latency unavailable; external host collector/witness overhead separate.",
                    "cleanup_errors": cleanup_errors,
                },
            )
            if cleanup_errors:
                raise RuntimeError("actor cleanup failed: " + str(cleanup_errors))
            if errors:
                raise RuntimeError("workload/collector failed") from errors[0]
        if result is not None:
            result["passed"] = True
            result["finalized_after_cleanup"] = True
            ingestion.save(root / "result.json", result)
            print(
                "Live synchronous journal scenario passed:",
                root,
                result["proof"],
                flush=True,
            )
            return root


if __name__ == "__main__":
    run()
