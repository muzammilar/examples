"""Continuous Quack observers while the host restarts one retained-disk guest."""

import csv
import gzip
import json
import multiprocessing as mp
import os
from pathlib import Path
import resource
import signal
import shlex
import subprocess
import sys
import time
import traceback

import history
import lab

ARTIFACTS = """import hashlib,json,pathlib,sys,_duckdb,duckdb
c=duckdb.connect(); c.execute(\"SET extension_directory='/opt/duckdb_extensions'\"); c.execute('LOAD quack')
files={'engine':pathlib.Path(_duckdb.__file__),'server_source':pathlib.Path(sys.argv[1])}
files.update({'quack/'+str(p.relative_to('/opt/duckdb_extensions')):p for p in pathlib.Path('/opt/duckdb_extensions').rglob('*.duckdb_extension')})
digests={}
for name,p in files.items():
 with p.open('rb') as stream:digests[name]=hashlib.file_digest(stream,'sha256').hexdigest()
print(json.dumps({'sha256':digests,'versions':c.execute(\"SELECT version(),(SELECT extension_version FROM duckdb_extensions() WHERE extension_name='quack')\").fetchall()}))
"""


def query(connection, sql):
    return connection.execute(
        "CALL quack_query(?, ?, token := 'local-lab-token', disable_ssl := true)",
        ["quack:primary:9494", sql],
    )


def connection(timeout=3):
    # No mutation retries: a lost response is ambiguous, and the next request uses
    # a new range. Connection establishment is retried separately by each actor.
    value = lab.duckdb.connect()
    value.execute("SET enable_progress_bar=false")
    value.execute("SET threads=2")
    value.execute("SET memory_limit='256MB'")
    value.execute("SET extension_directory='/opt/duckdb_extensions'")
    value.execute("LOAD quack")
    value.execute("SET http_retries=0")
    value.execute(f"SET http_timeout={int(timeout)}")
    return value


def insert_sql(table, first, end, customers):
    return (
        f"INSERT INTO {table} SELECT i,CAST(i%{customers} AS INTEGER),i%10000+1,"
        f"sha256(CAST(i AS VARCHAR)),{first},{end} FROM range({first},{end}) t(i)"
    )


def resource_usage():
    value = resource.getrusage(resource.RUSAGE_SELF)
    return {
        "pid": os.getpid(),
        "user_cpu_seconds": value.ru_utime,
        "system_cpu_seconds": value.ru_stime,
        "peak_rss_bytes": value.ru_maxrss * 1024,
    }


def writer(root, config, worker, stop):
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    journal = f"writer-{worker}.jsonl"
    value = None
    with (root / journal).open("x") as stream:
        seq = 0
        while not stop.is_set():
            first = (
                config["rows"]
                + (seq * config["clients"] + worker) * config["batch_rows"]
            )
            end, seq = first + config["batch_rows"], seq + 1
            started = time.monotonic_ns()
            event = {
                "first": first,
                "end": end,
                "worker": worker,
                "seq": seq,
                "started_ns": started,
                "started_utc_ns": time.time_ns(),
                "journal": journal,
            }
            history.emit(stream, dict(event, kind="intent"))
            rpc_started = time.monotonic_ns()
            submitted = False
            try:
                if value is None:
                    value = connection()
                submitted = True
                query(
                    value, insert_sql(config["table"], first, end, config["customers"])
                ).fetchall()
                outcome = {"kind": "ok"}
            except Exception as error:
                outcome = {
                    "kind": "ambiguous" if submitted else "fail",
                    "error": str(error)[:1500],
                }
                if value is not None:
                    value.close()
                value = None
            history.emit(
                stream,
                dict(
                    event,
                    **outcome,
                    elapsed_ns=time.monotonic_ns() - rpc_started,
                    intent_to_response_ns=time.monotonic_ns() - started,
                    ended_ns=time.monotonic_ns(),
                    ended_utc_ns=time.time_ns(),
                ),
            )
            stop.wait(config["delay_seconds"])
    if value is not None:
        value.close()
    history.save(root / f"writer-{worker}-resources.json", resource_usage())


def reader(root, config, stop):
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    value, request = None, 0
    acknowledgements = history.Acknowledgements(root)
    with (root / "reader.jsonl").open("x") as stream:
        while not stop.is_set():
            request += 1
            acknowledged = acknowledgements.poll()
            seed = config["rows"]
            expected = [
                history.body(i, 0, seed, config["customers"]) for i in (0, seed - 1)
            ]
            if acknowledged:
                expected += [
                    history.body(
                        i,
                        acknowledged["first"],
                        acknowledged["end"],
                        config["customers"],
                    )
                    for i in (acknowledged["first"], acknowledged["end"] - 1)
                ]
            expected.sort()
            started = time.monotonic_ns()
            event = {
                "request": request,
                "started_ns": started,
                "started_utc_ns": time.time_ns(),
                "expected": expected,
                "acknowledged_ended_ns": acknowledged["ended_ns"]
                if acknowledged
                else None,
            }
            try:
                if value is None:
                    value = connection()
                actual = [
                    list(row)
                    for row in query(
                        value,
                        f"SELECT * FROM {config['table']} WHERE event_id IN ({','.join(str(r[0]) for r in expected)}) ORDER BY event_id",
                    ).fetchall()
                ]
                outcome = {
                    "kind": "ok" if actual == expected else "mismatch",
                    "actual": actual,
                }
            except Exception as error:
                outcome = {"kind": "fail", "error": str(error)[:1500]}
                if value is not None:
                    value.close()
                value = None
            history.emit(
                stream,
                dict(
                    event,
                    **outcome,
                    elapsed_ns=time.monotonic_ns() - started,
                    ended_ns=time.monotonic_ns(),
                    ended_utc_ns=time.time_ns(),
                ),
            )
            if outcome["kind"] == "mismatch":
                stop.set()
                raise RuntimeError("wrong acknowledged body during live read")
            stop.wait(config["reader_delay_seconds"])
    if value is not None:
        value.close()
    history.save(root / "reader-resources.json", resource_usage())


def phase(root, name):
    value = {
        "name": name,
        "monotonic_ns": time.monotonic_ns(),
        "utc_ns": time.time_ns(),
    }
    with (root / "phases.jsonl").open("a") as stream:
        history.emit(stream, value)
    print(name, flush=True)


def progress(root, processes, since=0, minimum=8, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(not p.is_alive() for p in processes):
            raise RuntimeError("a live actor exited before recovery completed")
        writers = [
            e for p in root.glob("writer-*.jsonl") for e in history.events(p, False)
        ]
        reads = (
            history.events(root / "reader.jsonl", False)
            if (root / "reader.jsonl").exists()
            else []
        )
        if any(e["kind"] == "mismatch" for e in reads):
            raise RuntimeError("reader mismatch")
        if (
            sum(e["kind"] == "ok" and e["started_ns"] >= since for e in writers)
            >= minimum
            and all(
                sum(
                    e["kind"] == "ok"
                    and e["started_ns"] >= since
                    and e["worker"] == worker
                    for e in writers
                )
                >= 2
                for worker in range(len(processes) - 1)
            )
            and sum(e["kind"] == "ok" and e["started_ns"] >= since for e in reads)
            >= minimum
        ):
            return
        time.sleep(0.2)
    raise RuntimeError("live successful read/write progress deadline exceeded")


def guest_state():
    value = {
        "boot_id": lab.ssh("primary", "cat /proc/sys/kernel/random/boot_id"),
        "service": lab.ssh(
            "primary",
            "systemctl show ducklab.service -p MainPID -p ExecMainStartTimestampMonotonic -p ActiveState",
        ),
        "pool": lab.healthy_pool("primary"),
        "storage": lab.ssh(
            "primary",
            "zfs get -Hp -o name,property,value used,logicalused,referenced,logicalreferenced,compressratio,compression,sync,atime,recordsize ducklab/data; du -B1 /srv/ducklab/data/events.duckdb*; du --apparent-size -B1 /srv/ducklab/data/events.duckdb*",
        ),
        "memory": lab.ssh(
            "primary", "cat /proc/meminfo; cat /proc/spl/kstat/zfs/arcstats"
        ),
        "block_io": lab.ssh("primary", "cat /proc/diskstats; zpool iostat -Hp ducklab"),
        "zfs_arc_max_bytes": int(
            lab.ssh("primary", "cat /sys/module/zfs/parameters/zfs_arc_max")
        ),
    }
    value["arc_c_max_bytes"] = history.verify_arc(
        value["memory"], value["zfs_arc_max_bytes"], lab.ARC_MAX
    )
    return value


def export(root, config, name="rows.csv", witness_root=None):
    path = root / name
    with connection(300) as value, path.open("x", newline="") as stream:
        result = query(value, f"SELECT * FROM {config['table']} ORDER BY event_id")
        writer = csv.writer(stream)
        writer.writerow([item[0] for item in result.description])
        while rows := result.fetchmany(2048):
            writer.writerows(rows)
        stream.flush()
        os.fsync(stream.fileno())
    proof = history.reconcile(path, witness_root or root, config)
    started, cpu = time.monotonic_ns(), time.process_time_ns()
    compressed = 0

    # Compressibility is a separate off-path layer, not native ZFS compression.
    class Sink:
        def write(self, data):
            nonlocal compressed
            compressed += len(data)

    with (
        path.open("rb") as stream,
        gzip.GzipFile(
            fileobj=Sink(), mode="wb", compresslevel=1, mtime=0
        ) as compressor,
    ):
        while block := stream.read(1024**2):
            compressor.write(block)
    proof["gzip1"] = {
        "compressed_bytes": compressed,
        "ratio": proof["csv_bytes"] / compressed,
        "wall_ns": time.monotonic_ns() - started,
        "cpu_ns": time.process_time_ns() - cpu,
        "scope": "Off-path exported CSV gzip1, including file reads. Not engine or ZFS compression.",
    }
    return proof


def run(root):
    config = json.loads((root / "config.json").read_text())
    table = config["table"]
    if table != "live_" + root.name or not root.name.isalnum():
        raise ValueError("case/table mismatch")
    stop = mp.get_context("spawn").Event()
    processes = []

    def interrupted(*_):
        stop.set()
        raise KeyboardInterrupt("stop requested")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        phase(root, "configuration")
        lab.configure_arc("primary")
        generation = lab.ssh("primary", "zfs get -Hp -o value guid ducklab/data")
        history.save(root / "generation.json", {"dataset_guid": generation})
        actual = json.loads(
            lab.ssh(
                "primary",
                "/opt/venv/bin/python -c "
                + shlex.quote(ARTIFACTS)
                + " /opt/ducklab/server.py",
            )
        )
        local = json.loads(
            subprocess.run(
                [sys.executable, "-c", ARTIFACTS, "/opt/lab/app/server.py"],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            ).stdout
        )
        if actual != local:
            raise RuntimeError(
                "actual guest engine, extension or server source differs from controller artifacts"
            )
        history.save(
            root / "native-artifacts.json", {"guest": actual, "controller": local}
        )
        phase(root, "seed")
        with connection(300) as value:
            query(
                value,
                f"CREATE TABLE {table}(event_id BIGINT PRIMARY KEY,customer_id INTEGER,amount_cents BIGINT,payload VARCHAR,batch_start BIGINT,batch_end BIGINT)",
            ).fetchall()
            history.save(
                root / "seed-intent.json",
                {
                    "rows": config["rows"],
                    "customers": config["customers"],
                    "started_ns": time.monotonic_ns(),
                },
            )
            query(
                value, insert_sql(table, 0, config["rows"], config["customers"])
            ).fetchall()
            history.save(
                root / "seed-ack.json",
                {"rows": config["rows"], "ended_ns": time.monotonic_ns()},
            )
            history.save(
                root / "settings.json",
                {
                    "administrative_client": value.execute(
                        "SELECT name,value FROM duckdb_settings() WHERE name IN ('http_retries','http_timeout','threads','memory_limit')"
                    ).fetchall(),
                    "versions": query(
                        value,
                        "SELECT version(), (SELECT extension_version FROM duckdb_extensions() WHERE extension_name='quack')",
                    ).fetchall(),
                    "native_engine": query(
                        value,
                        "SELECT name,value FROM duckdb_settings() WHERE name IN ('memory_limit','threads','checkpoint_threshold','wal_autocheckpoint','fsync_mode')",
                    ).fetchall(),
                },
            )
        with connection() as value:
            history.save(
                root / "workload-settings.json",
                value.execute(
                    "SELECT name,value FROM duckdb_settings() WHERE name IN ('http_retries','http_timeout','threads','memory_limit')"
                ).fetchall(),
            )
        history.save(root / "before.json", guest_state())
        phase(root, "baseline")
        context = mp.get_context("spawn")
        processes = [
            context.Process(
                target=writer, args=(root, config, i, stop), name=f"live-writer-{i}"
            )
            for i in range(config["clients"])
        ]
        processes.append(
            context.Process(
                target=reader, args=(root, config, stop), name="live-reader"
            )
        )
        for process in processes:
            process.start()
        progress(root, processes)
        time.sleep(config["baseline_seconds"])
        progress(root, processes)
        wal = int(lab.ssh("primary", "stat -c %s /srv/ducklab/data/events.duckdb.wal"))
        if wal <= 0:
            raise RuntimeError("active WAL missing before VM cut")
        phase(root, "fault")
        lab.qmp_quit("primary")
        history.save(
            root / "restart-request.json",
            {
                "wal_bytes": wal,
                "actor_pids": [p.pid for p in processes],
                "requested_ns": time.monotonic_ns(),
            },
        )
        deadline = time.monotonic() + 900
        while not (root / "restart-complete.json").exists():
            if time.monotonic() > deadline or any(not p.is_alive() for p in processes):
                raise RuntimeError("host restart or live-actor deadline exceeded")
            time.sleep(0.2)
        phase(root, "native-recovery")
        boot_arc = int(lab.ssh("primary", "cat /sys/module/zfs/parameters/zfs_arc_max"))
        history.save(root / "arc-after-boot.json", {"zfs_arc_max_bytes": boot_arc})
        if boot_arc != lab.ARC_MAX:
            raise RuntimeError("persisted ZFS ARC option did not survive guest boot")
        lab.ssh(
            "primary",
            "if ! zpool list ducklab >/dev/null 2>&1; then zpool import -d /dev/disk/by-id ducklab; fi",
        )
        lab.start_server("primary")
        after = guest_state()
        if (
            after["boot_id"]
            == json.loads((root / "before.json").read_text())["boot_id"]
        ):
            raise RuntimeError("guest boot identity unchanged")
        history.save(root / "recovered.json", after)
        recovered = time.monotonic_ns()
        phase(root, "recovered")
        progress(root, processes, recovered)
        time.sleep(config["recovered_seconds"])
        progress(root, processes, recovered)
        stop.set()
        for process in processes:
            process.join(30)
            if process.is_alive() or process.exitcode:
                raise RuntimeError("live actor failed or could not stop")
        phase(root, "verify")
        history.save(root / "settled.json", guest_state())
        proof = export(root, config)
        previous = []
        for path in sorted(root.parent.glob("*/generation.json")):
            if (
                path.parent == root
                or json.loads(path.read_text())["dataset_guid"] != generation
            ):
                continue
            prior = path.parent
            old_config = json.loads((prior / "config.json").read_text())
            old = json.loads((prior / "result.json").read_text())["proof"]
            checked = export(root, old_config, "prior-" + prior.name + ".csv", prior)
            if {key: value for key, value in checked.items() if key != "gzip1"} != {
                key: value for key, value in old.items() if key != "gzip1"
            }:
                raise RuntimeError("earlier case lost or changed acknowledged state")
            previous.append({"case": prior.name, "proof": checked})
        phase(root, "complete")
        history.save(
            root / "result.json",
            {
                "passed": True,
                "proof": proof,
                "prior_cases": previous,
                "phases": history.summaries(root),
                "actors": [{"pid": p.pid, "exit_code": p.exitcode} for p in processes],
                "controller": resource_usage(),
            },
        )
    except BaseException:
        history.save(
            root / "failure.json",
            {
                "passed": False,
                "error": traceback.format_exc(),
                "utc_ns": time.time_ns(),
            },
        )
        raise
    finally:
        stop.set()
        for process in processes:
            if process.is_alive():
                process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(10)


if __name__ == "__main__":
    run(Path(sys.argv[1]))
