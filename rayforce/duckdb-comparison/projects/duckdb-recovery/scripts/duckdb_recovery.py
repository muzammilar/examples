"""Native DuckDB/Quack process recovery, with independent C++ SQL sessions."""

import csv
import fcntl
import hashlib
import json
import os
import resource
import subprocess
import threading
import time
import uuid

import duckdb_live as lab
import experiment as bench
import ingestion
from journal import NativeIPC
from journal_restore import archive_compressibility, fingerprint
from journal_volume import SAMPLE
from live_journal import Witness, reconcile, recovery_observations, summarize


def exact_row_query(row):
    if len(row) != 6 or any(type(value) is not int for value in row):
        raise ValueError("expected six integer trade fields")
    fields = ("id", "customer", "symbol", "day", "qty", "price")
    return "SELECT count(*) FROM trades WHERE " + " AND ".join(
        f"{name}={value}" for name, value in zip(fields, row, strict=True)
    )


def require_progress(events, phase):
    for kind in ("write", "read"):
        if not any(
            event["kind"] == kind
            and event["phase"] == phase
            and event.get("status") == "ok"
            for event in events
        ):
            raise RuntimeError("missing independent progress: " + phase + "/" + kind)


def run():
    lab.require_empty()
    fixture = bench.prepare()
    root = bench.RESULTS / "duckdb-live" / uuid.uuid4().hex
    root.mkdir(parents=True)
    env = dict(
        os.environ,
        LAB_UID=str(os.getuid()),
        LAB_GID=str(os.getgid()),
        DUCKDB_VOLUME="duckdb-state-" + root.name,
    )
    baseline = bench.integer("JOURNAL_BASELINE_S", 10, 5, 300)
    outage = bench.integer("JOURNAL_OUTAGE_S", 5, 4, 60)
    recovery = bench.integer("JOURNAL_RECOVERY_S", 15, 10, 300)
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    witness = Witness(root / "history.jsonl")
    phase = "setup"
    stop, collector_stop = threading.Event(), threading.Event()
    actors, threads, errors, acknowledged = [], [], [], []
    names, actor_starts = {}, {}
    exported, server, collector = False, None, None
    resource_log = (root / "resources.jsonl").open("x")
    tracked = [
        'projects/duckdb-recovery/scripts/duckdb_recovery.py',
        'projects/duckdb-recovery/scripts/duckdb_live.py',
        'projects/rayforce-journal/scripts/journal.py',
        'projects/rayforce-journal/scripts/live_journal.py',
        'scripts/experiment.py',
        'projects/ingestion/scripts/ingestion.py',
        'projects/rayforce-journal/scripts/journal_restore.py',
        'projects/rayforce-journal/scripts/journal_volume.py',
        "deploy/compose/docker-compose.duckdb-live.yml",
        'config/quack-sources.json',
        'scripts/prepare.py',
        "docker/Dockerfile",
        "analytics-workload/cpp/duckdb_quack.cpp",
        "analytics-workload/CMakeLists.txt",
    ]
    ingestion.save(
        root / "setup.json",
        {
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "host": list(os.uname()),
            "cpu_model": bench.capture(
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            if os.uname().sysname == "Darwin"
            else "Unresolved; supply native CPU inventory",
            "fixture": fixture,
            "docker": json.loads(
                bench.capture(["docker", "info", "--format", "{{json .}}"], text=True)
            ),
            "sources": json.loads((bench.ROOT / "config/quack-sources.json").read_text()),
            "source_sha256": {
                name: hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest()
                for name in tracked
            },
            "timings_s": {"baseline": baseline, "outage": outage, "recovery": recovery},
            "client": {
                "write_max_per_s": 50,
                "read_max_per_s": 20,
                "arrival": "paced closed-loop",
                "read_write_timeout_s": 3,
                "control_timeout_s": 180,
                "mutation_retries": 0,
                "http_retries": 0,
                "tls": "trusted native log certificate fingerprint; explicit TLS",
                "sessions": "independent C++ control/writer/reader, one request in flight each",
            },
            "contract": {
                "ack": "successful autocommit INSERT after native WAL flush",
                "read": "all six fields of acknowledged or historical seed row",
                "fault": "sole owner SIGKILL, retained Linux volume, twice",
                "expected": {
                    "baseline_and_post_checkpoint": "both actors progress without errors",
                    "each_outage": "both actors observe errors; in-flight completion at transition allowed",
                    "each_recovery": "both actors resume within the configured observation window",
                    "reconciliation": "zero missing, wrong or duplicate seed/customer/ACK bodies",
                },
                "not_qualified": [
                    "HA",
                    "device loss",
                    "power loss",
                    "capacity",
                    "cold OS cache",
                ],
            },
            "sampling": "200 ms wait plus Docker stats/disk inspection overhead; timestamps retained",
        },
    )

    def mark(label):
        nonlocal phase
        phase = label
        witness.add(kind="phase", phase=phase)

    def hold(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if errors:
                raise RuntimeError("background workload/resource failure") from errors[
                    0
                ]
            time.sleep(0.05)

    def sample():
        endpoint = bench.docker_socket()
        try:
            while not collector_stop.is_set():
                record = {
                    "monotonic_ns": time.monotonic_ns(),
                    "phase": phase,
                    "roles": {},
                }
                for role, identifier in list(names.items()):
                    info = lab.inspect(identifier)
                    record["roles"][role] = {
                        "running": info["State"]["Running"],
                        "started_at": info["State"]["StartedAt"],
                        "sample": bench.resource_sample(endpoint, identifier)
                        if info["State"]["Running"]
                        else None,
                    }
                if server is not None:
                    info = lab.inspect(server)
                    record["disk"] = {"available": False, "reason": "server stopped"}
                    if info["State"]["Running"]:
                        try:
                            record["disk"] = json.loads(
                                bench.capture(
                                    ["docker", "exec", server, "python", "-c", SAMPLE],
                                    text=True,
                                )
                            )
                        except subprocess.CalledProcessError:
                            after = lab.inspect(server)
                            if (
                                after["State"]["Running"]
                                and after["State"]["StartedAt"]
                                == info["State"]["StartedAt"]
                            ):
                                raise
                            record["disk"] = {
                                "available": False,
                                "reason": "generation changed during observation",
                            }
                resource_log.write(json.dumps(record, separators=(",", ":")) + "\n")
                resource_log.flush()
                collector_stop.wait(0.2)
        except Exception as error:
            errors.append(error)
            stop.set()

    def actor(role, timeout=3):
        name = "duckdb-" + role + "-" + root.name[:8]
        connection = NativeIPC(
            lab.COMPOSE,
            dict(env, DUCKDB_RPC_TIMEOUT_S=str(timeout)),
            name,
            root / (role + ".stderr"),
            timeout=timeout + 10,
        )
        actors.append(connection)
        reply = connection.query("SELECT 1")
        if reply["status"] != "ok" or reply["body"] != "1":
            raise RuntimeError("native session readiness failed: " + str(reply))
        info = lab.inspect(name)
        names[role], actor_starts[role] = info["Id"], info["State"]["StartedAt"]
        return connection

    def control(connection, sql, expected=None, ready=False):
        deadline = time.monotonic() + 30
        while True:
            reply = connection.query(sql)
            witness.add(kind="control", phase=phase, source=sql, **reply)
            if reply["status"] == "ok":
                if expected is not None and reply["body"] != expected:
                    raise RuntimeError("native SQL reply differs: " + str(reply))
                return reply
            if not ready or time.monotonic() > deadline:
                raise RuntimeError("native SQL failed: " + str(reply))
            time.sleep(0.1)

    def observe(label):
        for role, identifier in list(names.items()):
            info = lab.inspect(identifier)
            if role != "server" and info["State"]["StartedAt"] != actor_starts[role]:
                raise RuntimeError("independent session restarted: " + role)
            lab.snapshot(identifier, root, label + "-" + role, disk=role == "server")

    def writer(connection):
        try:
            identity = fixture["rows"]
            while not stop.is_set():
                beginning = time.monotonic()
                row = ingestion.trade(identity)
                intent = witness.add(kind="intent", phase=phase, id=identity, row=row)
                reply = connection.query(ingestion.insert("duckdb", [row]))
                if reply["status"] == "ok" and reply["body"] != "1":
                    raise RuntimeError("native INSERT did not acknowledge one row")
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
                stop.wait(max(0, 0.02 - (time.monotonic() - beginning)))
        except Exception as error:
            errors.append(error)
            stop.set()

    def reader(connection, first):
        try:
            sequence = 0
            while not stop.is_set():
                beginning = time.monotonic()
                row = acknowledged[-1] if sequence % 2 and acknowledged else first
                intent = witness.add(
                    kind="read-intent", phase=phase, id=row[0], sequence=sequence
                )
                reply = connection.query(exact_row_query(row))
                if reply["status"] == "ok" and reply["body"] != "1":
                    reply["status"] = "mismatch"
                witness.add(
                    kind="read",
                    phase=intent["phase"],
                    end_phase=phase,
                    id=row[0],
                    sequence=sequence,
                    invocation_ns=intent["monotonic_ns"],
                    temperature="historical_resident" if row is first else "recent_ack",
                    **reply,
                )
                if reply["status"] == "mismatch":
                    raise RuntimeError(
                        "successful read returned wrong acknowledged body"
                    )
                sequence += 1
                stop.wait(max(0, 0.05 - (time.monotonic() - beginning)))
        except Exception as error:
            errors.append(error)
            stop.set()

    try:
        lab.initialize(root, env)
        server, pin = lab.start(root, env)
        env["DUCKDB_FINGERPRINT"] = pin
        names["server"] = server
        collector = threading.Thread(target=sample, name="resources")
        collector.start()
        mark("seed")
        seeder = actor("control", 180)
        for sql in bench.queries("duckdb")[0]:
            control(seeder, sql)
        control(seeder, "SELECT count(*) FROM trades", str(fixture["rows"]))
        control(seeder, "SELECT count(*) FROM customers", "10000")
        control(seeder, "CHECKPOINT")
        control(
            seeder,
            "SELECT current_setting('threads'),current_setting('memory_limit'),current_setting('checkpoint_threshold')",
        )
        control(
            seeder,
            "SELECT column_name,compression,count(*) FROM pragma_storage_info('trades') GROUP BY 1,2 ORDER BY 1,2",
        )
        observe("seeded")
        coordinator = seeder
        writes, reads = (actor(role) for role in ("writer", "reader"))
        with (bench.RESULTS / "data/trades.csv").open() as source:
            first = list(map(int, next(csv.DictReader(source)).values()))
        control(coordinator, exact_row_query(first), "1")
        mark("baseline")
        for function, args in ((writer, (writes,)), (reader, (reads, first))):
            thread = threading.Thread(
                target=function, args=args, name=function.__name__
            )
            threads.append(thread)
            thread.start()
        hold(baseline)
        for cycle in (1, 2):
            require_progress(witness.events, phase)
            observe(f"cycle-{cycle}-before")
            native = json.loads(
                (root / f"cycle-{cycle}-before-server-native.json").read_text()
            )
            if not any(
                name.endswith(".wal") and value["bytes"] > 0
                for name, value in native["state_disk"]["files"].items()
            ):
                raise RuntimeError("no native WAL tail was observed before crash")
            previous = lab.inspect(server)
            mark(f"outage-{cycle}")
            subprocess.run(
                ["docker", "kill", "--signal", "KILL", server], check=True, timeout=30
            )
            dead = lab.inspect(server)
            ingestion.save(root / f"cycle-{cycle}-killed.json", dead)
            if dead["State"]["Running"] or dead["State"]["ExitCode"] != 137:
                raise RuntimeError("SIGKILL termination not established")
            hold(outage)
            mark(f"recovery-{cycle}")
            subprocess.run(["docker", "start", server], check=True, timeout=30)
            control(coordinator, "SELECT 1", "1", ready=True)
            if (
                lab.inspect(server)["State"]["StartedAt"]
                == previous["State"]["StartedAt"]
            ):
                raise RuntimeError("server generation did not change")
            hold(recovery)
            require_progress(witness.events, phase)
            observe(f"cycle-{cycle}-recovered")
            if cycle == 1:
                mark("checkpoint-under-load")
                control(coordinator, "FORCE CHECKPOINT")
                mark("second-snapshot-tail")
                hold(baseline)
        stop.set()
        for thread in threads:
            thread.join(timeout=15)
            if thread.is_alive():
                raise RuntimeError("independent worker failed to stop")
        if errors:
            raise RuntimeError("background failure") from errors[0]
        for healthy in ("baseline", "second-snapshot-tail"):
            require_progress(witness.events, healthy)
            if any(
                e["kind"] in {"write", "read"}
                and e["phase"] == healthy
                and e["status"] != "ok"
                for e in witness.events
            ):
                raise RuntimeError("unexpected healthy-phase request error: " + healthy)
        for failed_phase in ("outage-1", "outage-2"):
            for kind in ("write", "read"):
                if not any(
                    e["kind"] == kind
                    and e["phase"] == failed_phase
                    and e.get("status") == "error"
                    for e in witness.events
                ):
                    raise RuntimeError(
                        "missing independent outage observation: "
                        + failed_phase
                        + "/"
                        + kind
                    )
        mark("reconciliation")
        control(
            seeder,
            "COPY (SELECT * FROM trades ORDER BY id) TO '/state/recovered.csv' (HEADER)",
        )
        control(
            seeder,
            "COPY (SELECT * FROM customers ORDER BY customer) TO '/state/customers.csv' (HEADER)",
        )
        observe("final")
        collector_stop.set()
        collector.join(timeout=30)
        if collector.is_alive() or errors:
            raise RuntimeError("resource collector did not complete cleanly")
        for connection in list(actors):
            connection.close()
            actors.remove(connection)
        state = lab.export(server, root)
        exported = True
        events = [
            json.loads(line)
            for line in (root / "history.jsonl").read_text().splitlines()
        ]
        proof = reconcile(
            bench.RESULTS / "data/trades.csv", state / "recovered.csv", events
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
        files = sorted(
            p
            for p in state.iterdir()
            if p.is_file() and p.name.startswith("events.duckdb")
        )
        (root / "server-final.log").write_text(
            bench.capture(
                ["docker", "logs", server], text=True, stderr=subprocess.STDOUT
            )
        )
        result = {
            "passed": False,
            "scenario_passed": True,
            "proof": proof,
            "phases": summarize(events),
            "read_temperature_phases": summarize(
                [
                    dict(event, phase=event["phase"] + "/" + event["temperature"])
                    for event in events
                    if event["kind"] == "read"
                ]
            ),
            "recovery_observations": recovery_observations(events),
            "native_files": fingerprint(files),
            "archive_compressibility": archive_compressibility(files),
            "wall_ns": time.monotonic_ns() - started,
            "scope": "Two retained-Linux-volume sole-owner SIGKILLs with live independent SQL sessions and a checkpoint under load; correctness diagnostic, not capacity/HA/power-loss qualification",
        }
        ingestion.save(root / "result.json", result)
    except Exception as error:
        ingestion.save(
            root / "failure.json",
            {"passed": False, "error": repr(error), "phase": phase},
        )
        raise
    finally:
        stop.set()
        for thread in threads:
            thread.join(timeout=15)
        collector_stop.set()
        if collector:
            collector.join(timeout=30)
        cleanup_errors = []
        if any(t.is_alive() for t in threads) or (collector and collector.is_alive()):
            cleanup_errors.append(
                "background worker did not terminate; preserve its containers"
            )
        else:
            for connection in actors:
                try:
                    connection.close()
                except Exception as error:
                    cleanup_errors.append(repr(error))
            if server and not exported:
                try:
                    lab.export(server, root)
                    exported = True
                except Exception as error:
                    cleanup_errors.append("failed state export: " + repr(error))
            try:
                lab.call(
                    env,
                    "down",
                    "--remove-orphans",
                    *(["--volumes"] if exported else []),
                )
            except Exception as error:
                cleanup_errors.append(repr(error))
        resource_log.close()
        witness.close()
        usage = resource.getrusage(resource.RUSAGE_SELF)
        ingestion.save(
            root / "controller.json",
            {
                "wall_ns": time.monotonic_ns() - started,
                "cpu_ns": time.process_time_ns() - cpu,
                "peak_rss_bytes": usage.ru_maxrss
                * (1 if os.uname().sysname == "Darwin" else 1024),
                "cleanup_errors": cleanup_errors,
            },
        )
        if cleanup_errors:
            raise RuntimeError(
                "cleanup failed; evidence retained: " + repr(cleanup_errors)
            )
    lab.require_empty()
    result["passed"] = True
    ingestion.save(root / "result.json", result)
    print("DuckDB native live recovery passed:", root, flush=True)


if __name__ == "__main__":
    bench.RESULTS.mkdir(exist_ok=True)
    with (bench.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run()
