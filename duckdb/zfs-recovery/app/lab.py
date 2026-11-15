"""Orchestrate owned VM storage, independent DuckDB clients and recovery checks."""

import concurrent.futures
import json
import multiprocessing as mp
import os
import re
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import tempfile
import time

import duckdb

RESULTS = Path("/results")
SSH = [
    "ssh",
    "-i",
    "/keys/id_ed25519",
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "LogLevel=ERROR",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=15",
    "-o",
    "ServerAliveInterval=15",
    "-o",
    "ServerAliveCountMax=6",
    "-p",
    "2222",
]
NODES = ("primary", "standby")


def ssh_args(node, command):
    if node not in NODES:
        raise ValueError("unknown node")
    return SSH + ["lab@" + node, shlex.join(["sudo", "-n", "bash", "-euc", command])]


def ssh(node, command, timeout=120):
    p = subprocess.run(
        ssh_args(node, command), capture_output=True, text=True, timeout=timeout
    )
    if p.returncode:
        raise RuntimeError(
            f"{node} exit={p.returncode}: {command}\n{p.stdout[-500:]}\n{p.stderr[-3000:]}"
        )
    return p.stdout.strip()


def integer(name, default, minimum=1):
    value = int(os.environ.get(name, str(default)))
    if not minimum <= value <= 100000000:
        raise ValueError(f"{name} out of range")
    return value


def save(name, value):
    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / (name + ".json")).open("w") as f:
        json.dump(value, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    print(name, json.dumps(value), flush=True)


def connect(node):
    c = duckdb.connect()
    c.execute("SET enable_progress_bar=false")
    c.execute("SET threads=2")
    c.execute("SET memory_limit='256MB'")
    c.execute("SET extension_directory='/opt/duckdb_extensions'")
    c.execute("LOAD quack")
    c.execute(
        f"ATTACH 'quack:{node}:9494' AS remote (TOKEN 'local-lab-token', DISABLE_SSL true)"
    )
    return c


def ready(node, timeout=90):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            with connect(node) as c:
                c.execute("SELECT 1").fetchone()
            return
        except Exception as e:
            last = e
            time.sleep(1)
    raise RuntimeError(f"{node} Quack not ready: {last}")


def start_server(node):
    ssh(node, "systemctl start ducklab.service")
    ready(node)


def provision():
    if (
        ssh(
            "standby",
            "test ! -f /var/lib/ducklab-promoted && echo fresh || echo promoted",
        )
        != "fresh"
    ):
        raise RuntimeError(
            "standby was promoted; use the complete clean lifecycle rather than restarting the stale primary"
        )
    for node in NODES:
        print(f"{node}: installing offline runtime and ZFS packages", flush=True)
        # The artifacts are pinned and installed once in the image, then copied offline.
        files = [
            "/opt/python-runtime.tar.gz",
            "/opt/zfs-debs.tar.gz",
            "/opt/extensions.tar.gz",
            "/opt/lab/app/server.py",
        ]
        scp = [
            "scp",
            "-q",
            "-i",
            "/keys/id_ed25519",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-P",
            "2222",
        ]
        subprocess.run(
            scp + files + ["lab@" + node + ":/tmp/"], check=True, timeout=180
        )
        ssh(
            node,
            """
mkdir -p /opt/ducklab
tar xzf /tmp/python-runtime.tar.gz -C /opt
mkdir -p /tmp/ducklab-debs
tar xzf /tmp/zfs-debs.tar.gz -C /tmp/ducklab-debs
dpkg --install /tmp/ducklab-debs/*.deb
tar xzf /tmp/extensions.tar.gz -C /opt
cp /tmp/server.py /opt/ducklab/server.py
printf 268435456 > /sys/module/zfs/parameters/zfs_arc_max
if ! zpool list ducklab >/dev/null 2>&1; then
  zpool create -o ashift=12 -O compression=lz4 -O atime=off -O recordsize=256K -O mountpoint=/srv/ducklab ducklab mirror /dev/disk/by-id/virtio-ducklab-a /dev/disk/by-id/virtio-ducklab-b
fi
if ! zfs list ducklab/data >/dev/null 2>&1; then zfs create ducklab/data; fi
cat > /etc/systemd/system/ducklab.service <<'UNIT'
[Unit]
Description=Example-owned DuckDB Quack server
After=zfs-mount.service
[Service]
ExecStart=/opt/venv/bin/python /opt/ducklab/server.py
Restart=no
TimeoutStopSec=10
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
# Deliberately not enabled at boot: a fenced old primary must not regain ownership.
""",
            timeout=600,
        )
        print(f"{node}: pool and service definition ready", flush=True)
    start_server("primary")
    save(
        "versions",
        {
            node: ssh(
                node,
                "uname -r; zfs version; /opt/venv/bin/python -c 'import duckdb; print(duckdb.__version__)'",
            )
            for node in NODES
        },
    )


def reset():
    if (
        ssh(
            "standby",
            "test ! -f /var/lib/ducklab-promoted && echo fresh || echo promoted",
        )
        != "fresh"
    ):
        raise RuntimeError(
            "previous failover retained; run docker-down then docker-up/up for a fresh test"
        )
    for node in NODES:
        ssh(
            node,
            "systemctl stop ducklab.service; if zfs list ducklab/readcopy >/dev/null 2>&1; then zfs destroy -r ducklab/readcopy; fi; if zfs list ducklab/data >/dev/null 2>&1; then zfs destroy -r ducklab/data; fi",
        )
    ssh("primary", "zfs create ducklab/data")
    start_server("primary")
    with connect("primary") as c:
        c.execute(
            "CALL quack_query(?, ?, token := 'local-lab-token', disable_ssl := true)",
            [
                "quack:primary:9494",
                "CREATE TABLE events (event_id BIGINT PRIMARY KEY,customer_id INTEGER,amount_cents BIGINT,batch_start BIGINT,batch_end BIGINT)",
            ],
        ).fetchall()


def writer(node, lo, hi, worker, clients, batch, customers, delay, label):
    path = RESULTS / f"{label}-{worker}.jsonl"
    failures = []
    with path.open("w") as journal:
        try:
            with connect(node) as c:
                for first in range(lo + worker * batch, hi, clients * batch):
                    end = min(first + batch, hi)
                    start = time.monotonic()
                    try:
                        c.execute(
                            "CALL quack_query(?, ?, token := 'local-lab-token', disable_ssl := true)",
                            [
                                f"quack:{node}:9494",
                                f"INSERT INTO events SELECT i,CAST(i%{customers} AS INTEGER),i%10000+1,{first},{end} FROM range({first},{end}) t(i)",
                            ],
                        ).fetchall()
                    except Exception as e:
                        failures.append(
                            {
                                "first_id": first,
                                "end_id": end,
                                "error": str(e)[:500],
                                "outcome": "ambiguous",
                            }
                        )
                        break
                    journal.write(
                        json.dumps(
                            {
                                "first_id": first,
                                "end_id": end,
                                "seconds": time.monotonic() - start,
                                "ack_monotonic": time.monotonic(),
                            }
                        )
                        + "\n"
                    )
                    journal.flush()
                    os.fsync(journal.fileno())
                    time.sleep(delay)
        except Exception as e:
            failures.append({"error": str(e)[:500], "outcome": "connect-failed"})
    (RESULTS / f"{label}-{worker}-errors.json").write_text(json.dumps(failures))


def spawn(node, lo, hi, label, delay):
    for p in RESULTS.glob(label + "-*"):
        if re.fullmatch(re.escape(label) + r"-\d+(?:-errors)?\.(?:jsonl|json)", p.name):
            p.unlink()
    clients = integer("CLIENTS", 4)
    if clients > 8:
        raise ValueError("CLIENTS must be 1..8 for this resource-limited lab")
    batch = integer("BATCH_ROWS", 10000)
    customers = integer("CUSTOMERS", 10000)
    processes = [
        mp.get_context("spawn").Process(
            target=writer,
            name=f"{label}-{w}",
            args=(node, lo, hi, w, clients, batch, customers, delay, label),
        )
        for w in range(clients)
    ]
    for p in processes:
        p.start()
    return processes


def join(processes, allow_errors=False):
    for p in processes:
        p.join(120)
        if p.is_alive():
            p.terminate()
            p.join()
            raise RuntimeError("client deadline exceeded")
        if p.exitcode != 0:
            raise RuntimeError(f"client process exit {p.exitcode}")
        if not allow_errors:
            errors = json.loads((RESULTS / (p.name + "-errors.json")).read_text())
            if errors:
                raise RuntimeError(f"{p.name} failed: {errors}")


def acknowledgements(labels):
    rows = []
    for label in labels:
        for p in RESULTS.glob(label + "-*.jsonl"):
            for line in p.read_text().splitlines():
                rows.append(json.loads(line))
    return rows


def wait_acks(labels, count, processes, timeout=120):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if sum(r["end_id"] - r["first_id"] for r in acknowledgements(labels)) >= count:
            return
        if not any(p.is_alive() for p in processes):
            raise RuntimeError(
                "all producers stopped before the fault/replication threshold"
            )
        time.sleep(0.1)
    raise RuntimeError("acknowledgement deadline exceeded")


def snapshot(node, name):
    ssh(node, "zfs snapshot ducklab/data@" + name)


def replicate(
    source, dest, name, previous=None, resume_token=None, interrupt_bytes=None
):
    send = "zfs send " + (
        "-t " + shlex.quote(resume_token)
        if resume_token
        else (("-i ducklab/data@" + previous + " ") if previous else "")
        + "ducklab/data@"
        + name
    )
    receive = "zfs receive -s -u -o readonly=on ducklab/data"
    start = time.monotonic()
    total = 0
    with tempfile.TemporaryFile() as se, tempfile.TemporaryFile() as re:
        producer = subprocess.Popen(
            ssh_args(source, send), stdout=subprocess.PIPE, stderr=se
        )
        consumer = subprocess.Popen(
            ssh_args(dest, receive),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=re,
        )
        try:
            while True:
                b = producer.stdout.read(65536)
                if not b:
                    break
                consumer.stdin.write(b)
                consumer.stdin.flush()
                total += len(b)
                if interrupt_bytes and total >= interrupt_bytes:
                    break
        finally:
            if interrupt_bytes:
                producer.terminate()
            producer.stdout.close()
            consumer.stdin.close()
        a = producer.wait(timeout=60)
        b = consumer.wait(timeout=60)
        if not interrupt_bytes and (a or b):
            se.seek(0)
            re.seek(0)
            raise RuntimeError(
                f"replication {a}/{b}: {se.read()[-1500:]} {re.read()[-1500:]}"
            )
    return {
        "bytes": total,
        "seconds": time.monotonic() - start,
        "interrupted": bool(interrupt_bytes),
    }


def clone_check(node, snapshot_name, expected=None):
    ssh(
        node,
        f"if zfs list ducklab/readcopy >/dev/null 2>&1; then zfs destroy ducklab/readcopy; fi; zfs clone -o readonly=off ducklab/data@{snapshot_name} ducklab/readcopy",
    )
    # Recover a crash-consistent DB+WAL on a writable clone before publishing it read-only.
    script = f"""import duckdb,json
c=duckdb.connect('/srv/ducklab/readcopy/events.duckdb');c.execute("SET enable_progress_bar=false");c.execute("SET threads=2");c.execute("SET memory_limit='256MB'");c.execute('CHECKPOINT');print(json.dumps(c.execute('SELECT count(*),count(DISTINCT event_id),sum(amount_cents),count(*) FILTER(WHERE event_id<0 OR event_id>={integer("ROWS", 2000000)} OR batch_start IS NULL OR batch_end IS NULL OR event_id<batch_start OR event_id>=batch_end OR customer_id IS NULL OR customer_id!=event_id%{integer("CUSTOMERS", 10000)} OR amount_cents IS NULL OR amount_cents!=event_id%10000+1) FROM events').fetchone()));partial=c.execute('SELECT count(*) FROM (SELECT batch_start,batch_end,count(*) n FROM events GROUP BY batch_start,batch_end HAVING n!=batch_end-batch_start)').fetchone()[0];assert partial==0,'partial committed batch';c.close()
"""
    q = "/opt/venv/bin/python -c " + shlex.quote(script)
    value = json.loads(ssh(node, q))
    ssh(node, "zfs set readonly=on ducklab/readcopy")
    if (
        value[0] != value[1]
        or value[3]
        or (expected is not None and value[0] != expected)
    ):
        raise RuntimeError(f"clone oracle failed: {value}")
    readonly = f"""import duckdb,json; c=duckdb.connect('/srv/ducklab/readcopy/events.duckdb',read_only=True); c.execute("SET enable_progress_bar=false"); c.execute("SET threads=2"); c.execute("SET memory_limit='256MB'"); print(json.dumps(c.execute('SELECT count(*),count(DISTINCT event_id),sum(amount_cents),count(*) FILTER(WHERE event_id<0 OR event_id>={integer("ROWS", 2000000)} OR batch_start IS NULL OR batch_end IS NULL OR event_id<batch_start OR event_id>=batch_end OR customer_id IS NULL OR customer_id!=event_id%{integer("CUSTOMERS", 10000)} OR amount_cents IS NULL OR amount_cents!=event_id%10000+1) FROM events').fetchone()))"""
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        checks = list(
            pool.map(
                lambda _: json.loads(
                    ssh(node, "/opt/venv/bin/python -c " + shlex.quote(readonly))
                ),
                range(2),
            )
        )
    if any(check != value for check in checks):
        raise RuntimeError("read-only clone readers disagree")
    return value


def qmp_quit(node):
    with socket.create_connection((node, 10099), timeout=5) as sock:
        f = sock.makefile("rwb")
        json.loads(f.readline())
        f.write(b'{"execute":"qmp_capabilities"}\n')
        f.flush()
        while "return" not in json.loads(f.readline()):
            pass
        f.write(b'{"execute":"quit"}\n')
        f.flush()
    # Confirm the old owner is inaccessible before starting the replacement.
    end = time.monotonic() + 15
    while time.monotonic() < end:
        try:
            with socket.create_connection((node, 9494), timeout=1):
                pass
        except OSError:
            return
        time.sleep(0.2)
    raise RuntimeError("primary was not fenced")


def oracle(node, labels, maximum):
    customers = integer("CUSTOMERS", 10000)
    with connect(node) as c:

        def query(sql):
            return c.execute(
                "CALL quack_query(?, ?, token := 'local-lab-token', disable_ssl := true)",
                [f"quack:{node}:9494", sql],
            ).fetchall()

        value = query(
            f"SELECT count(*),count(DISTINCT event_id),sum(amount_cents),count(*) FILTER(WHERE event_id IS NULL OR event_id<0 OR event_id>={maximum} OR batch_start IS NULL OR batch_end IS NULL OR event_id<batch_start OR event_id>=batch_end OR customer_id IS NULL OR customer_id!=event_id%{customers} OR amount_cents IS NULL OR amount_cents!=event_id%10000+1) FROM events"
        )[0]
        if value[0] != value[1] or value[3]:
            raise RuntimeError(f"row oracle failed: {value}")
        # One server-side grouping reconciles all batch acknowledgements without
        # pulling the database over the network or issuing one query per batch.
        groups = query(
            "SELECT batch_start,batch_end,count(*) FROM events GROUP BY batch_start,batch_end"
        )
        counts = {}
        for lo, hi, count in groups:
            if count != hi - lo:
                raise RuntimeError("partial committed batch after recovery")
            counts[(lo, hi)] = count
        missing_by_phase = {}
        for label in labels:
            missing_by_phase[label] = sum(
                r["end_id"]
                - r["first_id"]
                - counts.get((r["first_id"], r["end_id"]), 0)
                for r in acknowledgements([label])
            )
        missing = sum(missing_by_phase.values())
    return {
        "rows": value[0],
        "unique_ids": value[1],
        "amount_cents": value[2],
        "invalid_rows": value[3],
        "acknowledged_missing": missing,
        "missing_by_phase": missing_by_phase,
        "duplicate_ids": 0,
    }


def pool_state(node):
    fields = ssh(node, "zpool list -Hp -o size,alloc,free,health ducklab").split()
    detail = ssh(node, "zpool status -P ducklab")
    return {
        "size_bytes": int(fields[0]),
        "allocated_bytes": int(fields[1]),
        "free_bytes": int(fields[2]),
        "health": fields[3],
        "status": detail,
    }


def healthy_pool(node):
    state = pool_state(node)
    members = re.findall(
        r"virtio-ducklab-(?:a|b|replacement)(?:-part1)?\s+ONLINE\b", state["status"]
    )
    if (
        len(members) != 2
        or state["health"] != "ONLINE"
        or "mirror-0" not in state["status"]
        or "No known data errors" not in state["status"]
    ):
        raise RuntimeError(f"unhealthy mirror: {state}")
    return state


def disk_test():
    rows, batch = integer("ROWS", 2000000), integer("BATCH_ROWS", 10000)
    if rows < 20 * batch:
        raise ValueError("ROWS must be at least 20 * BATCH_ROWS")
    reset()
    before = healthy_pool("primary")
    if "virtio-ducklab-a" not in before["status"]:
        raise RuntimeError(
            "replacement already used; rerun the complete clean lifecycle"
        )
    producers = spawn(
        "primary", 0, rows, "disk", max(integer("BATCH_DELAY_MS", 1000, 0) / 1000, 0.2)
    )
    wait_acks(["disk"], 2 * batch, producers)
    ssh("primary", "zpool offline -f ducklab /dev/disk/by-id/virtio-ducklab-a")
    degraded = pool_state("primary")
    if degraded["health"] != "DEGRADED" or "FAULTED" not in degraded["status"]:
        raise RuntimeError("disk fault did not degrade mirror")
    acknowledged_at_fault = sum(
        r["end_id"] - r["first_id"] for r in acknowledgements(["disk"])
    )
    wait_acks(["disk"], acknowledged_at_fault + 2 * batch, producers)
    if not any(p.is_alive() for p in producers):
        raise RuntimeError("writers finished before replacement")
    start = time.monotonic()
    ssh(
        "primary",
        "zpool replace -w ducklab /dev/disk/by-id/virtio-ducklab-a /dev/disk/by-id/virtio-ducklab-replacement",
        timeout=300,
    )
    replacement_seconds = time.monotonic() - start
    join(producers)
    checked = oracle("primary", ["disk"], rows)
    if checked["rows"] != rows or checked["acknowledged_missing"]:
        raise RuntimeError("disk recovery lost acknowledged data")
    start = time.monotonic()
    ssh("primary", "zpool scrub -w ducklab", timeout=300)
    save(
        "disk-test",
        {
            "before": before,
            "degraded": degraded,
            "after": healthy_pool("primary"),
            "replacement_seconds": replacement_seconds,
            "scrub_seconds": time.monotonic() - start,
            "acknowledged_at_fault": acknowledged_at_fault,
            "additional_acknowledged_while_degraded": 2 * batch,
            "oracle": checked,
        },
    )


def crash_start():
    rows, batch = integer("ROWS", 2000000), integer("BATCH_ROWS", 10000)
    if rows < 20 * batch:
        raise ValueError("ROWS must be at least 20 * BATCH_ROWS")
    reset()
    healthy_pool("primary")
    join(spawn("primary", 0, rows // 2, "crash-seed", 0))
    producers = spawn(
        "primary",
        rows // 2,
        rows,
        "crash-live",
        max(integer("BATCH_DELAY_MS", 1000, 0) / 1000, 0.2),
    )
    wait_acks(["crash-live"], 2 * batch, producers)
    wal_bytes = int(ssh("primary", "stat -c %s /srv/ducklab/data/events.duckdb.wal"))
    if wal_bytes <= 0 or not any(p.is_alive() for p in producers):
        raise RuntimeError("crash needs an active WAL and writers")
    start = time.monotonic()
    qmp_quit("primary")
    join(producers, True)
    save(
        "crash-before",
        {
            "wal_bytes": wal_bytes,
            "crash_monotonic": start,
            "acknowledged_rows": sum(
                r["end_id"] - r["first_id"]
                for r in acknowledgements(["crash-seed", "crash-live"])
            ),
        },
    )


def crash_finish():
    rows, batch = integer("ROWS", 2000000), integer("BATCH_ROWS", 10000)
    before = json.loads((RESULTS / "crash-before.json").read_text())
    ssh(
        "primary",
        "if ! zpool list ducklab >/dev/null 2>&1; then zpool import -d /dev/disk/by-id ducklab; fi",
    )
    pool = healthy_pool("primary")
    start_server("primary")
    recovered = oracle("primary", ["crash-seed", "crash-live"], rows)
    if recovered["acknowledged_missing"]:
        raise RuntimeError("retained-disk crash lost acknowledged data")
    join(spawn("primary", 2 * rows, 2 * rows + 10 * batch, "crash-recovered", 0))
    after = oracle(
        "primary",
        ["crash-seed", "crash-live", "crash-recovered"],
        2 * rows + 10 * batch,
    )
    if after["acknowledged_missing"] or after["rows"] != recovered["rows"] + 10 * batch:
        raise RuntimeError("post-crash writes failed")
    save(
        "crash-test",
        {
            "before": before,
            "pool": pool,
            "recovered": recovered,
            "after": after,
            "first_recovered_write_seconds": min(
                r["ack_monotonic"] for r in acknowledgements(["crash-recovered"])
            )
            - before["crash_monotonic"],
            "client_errors": [
                json.loads(p.read_text())
                for p in RESULTS.glob("crash-live-*-errors.json")
            ],
        },
    )


def benchmark():
    rows, batch = integer("ROWS", 2000000), integer("BATCH_ROWS", 10000)
    extra = integer("BENCH_ROWS", 200000)
    runs = integer("QUERY_RUNS", 20)
    if runs > 1000:
        raise ValueError("QUERY_RUNS must be <=1000")
    # Run after replica-test, whose exact retained intervals are checked here.
    prior = oracle("standby", ["seed", "live", "promoted"], rows + 10 * batch)
    if prior["acknowledged_missing"] or prior["rows"] != 3 * rows // 4 + 10 * batch:
        raise RuntimeError("benchmark requires the completed replica-test dataset")
    start = time.monotonic()
    join(spawn("standby", 3 * rows, 3 * rows + extra, "benchmark", 0))
    elapsed = time.monotonic() - start
    checked = oracle("standby", ["benchmark"], 3 * rows + extra)
    if checked["acknowledged_missing"] or checked["rows"] != prior["rows"] + extra:
        raise RuntimeError("benchmark append oracle failed")
    intervals = [
        (0, 3 * rows // 4),
        (rows, rows + 10 * batch),
        (3 * rows, 3 * rows + extra),
    ]
    customer = min(42, integer("CUSTOMERS", 10000) - 1)
    result = {}
    with connect("standby") as c:
        for name, where in [
            ("aggregate", ""),
            ("customer", f" WHERE customer_id={customer}"),
        ]:
            ids = (
                i
                for lo, hi in intervals
                for i in range(lo, hi)
                if not where or i % integer("CUSTOMERS", 10000) == customer
            )
            expected_count = expected_sum = 0
            for i in ids:
                expected_count += 1
                expected_sum += i % 10000 + 1
            samples = []
            for trial in range(runs + 2):
                t = time.monotonic()
                value = c.execute(
                    "CALL quack_query(?, ?, token := 'local-lab-token', disable_ssl := true)",
                    [
                        "quack:standby:9494",
                        "SELECT count(*),sum(amount_cents) FROM events" + where,
                    ],
                ).fetchone()
                seconds = time.monotonic() - t
                if list(value) != [expected_count, expected_sum]:
                    raise RuntimeError(f"benchmark oracle failed: {value}")
                if trial >= 2:
                    samples.append(seconds)
            ordered = sorted(samples)
            result[name] = {
                "seconds": samples,
                "p50_seconds": ordered[(len(ordered) - 1) // 2],
                "p99_seconds": ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))],
                "rows": expected_count,
            }
    latencies = sorted(r["seconds"] for r in acknowledgements(["benchmark"]))
    save(
        "benchmark",
        {
            "queries": result,
            "query_runs": runs,
            "warmups": 2,
            "oracle": checked,
            "ingest_rows": extra,
            "ingest_seconds": elapsed,
            "ingest_rows_per_second": extra / elapsed,
            "batch_p50_seconds": latencies[(len(latencies) - 1) // 2],
            "batch_p99_seconds": latencies[
                min(len(latencies) - 1, int(len(latencies) * 0.99))
            ],
            "ingestion": "clients issuing server-generated SQL; includes process startup and external journal fsync; TCG emulation",
        },
    )


def replica_test():
    rows = integer("ROWS", 2000000)
    batch = integer("BATCH_ROWS", 10000)
    if rows < 20 * batch:
        raise ValueError(
            "ROWS must be at least 20 * BATCH_ROWS for an active-fault run"
        )
    delay = integer("BATCH_DELAY_MS", 1000, 0) / 1000
    reset()
    healthy_pool("primary")
    healthy_pool("standby")
    with connect("primary") as c:
        revision = c.execute(
            "SELECT extension_version FROM duckdb_extensions() WHERE extension_name='quack'"
        ).fetchone()[0]
    start = time.monotonic()
    base = rows // 2
    mid = 3 * rows // 4
    producers = spawn("primary", 0, base, "seed", 0)
    join(producers)
    first = oracle("primary", ["seed"], rows)
    if first["rows"] != base or first["acknowledged_missing"]:
        raise RuntimeError("seed failed")
    with connect("primary") as c:
        c.execute(
            "CALL quack_query('quack:primary:9494','CHECKPOINT',token := 'local-lab-token',disable_ssl := true)"
        )
    snapshot("primary", "baseline")
    full = replicate("primary", "standby", "baseline")
    clone = clone_check("standby", "baseline", base)
    # Snapshot while these independent processes are still appending.
    producers = spawn("primary", base, mid, "live", delay)
    wait_acks(["live"], 2 * batch, producers)
    if not any(p.is_alive() for p in producers):
        raise RuntimeError("no active writers at snapshot")
    snapshot("primary", "live")
    if not any(p.is_alive() for p in producers):
        raise RuntimeError(
            "writers finished before live snapshot completed; increase BATCH_DELAY_MS"
        )
    incremental = replicate("primary", "standby", "live", "baseline")
    join(producers)
    live = clone_check("standby", "live")
    # Resume token exercise on its own next snapshot, before any promotion.
    with connect("primary") as c:
        c.execute(
            "CALL quack_query('quack:primary:9494','CHECKPOINT',token := 'local-lab-token',disable_ssl := true)"
        )
    snapshot("primary", "resumable")
    interrupted = replicate(
        "primary", "standby", "resumable", "live", interrupt_bytes=65536
    )
    token = ssh("standby", "zfs get -H -o value receive_resume_token ducklab/data")
    if token == "-" or not token:
        raise RuntimeError("interruption did not produce a resume token")
    resumed = replicate("primary", "standby", "resumable", resume_token=token)
    last = clone_check("standby", "resumable", mid)
    # Explicit lag phase: no further replication, keep writing and cut the owner VM.
    producers = spawn("primary", mid, rows, "fault", max(delay, 0.2))
    wait_acks(["fault"], 2 * batch, producers)
    if not any(p.is_alive() for p in producers):
        raise RuntimeError("no active writers at power cut")
    fault = time.monotonic()
    qmp_quit("primary")
    join(producers, True)
    ssh(
        "standby",
        "touch /var/lib/ducklab-promoted; zfs destroy ducklab/readcopy; zfs set readonly=off ducklab/data; zfs mount ducklab/data",
    )
    start_server("standby")
    recovered = oracle("standby", ["seed", "live", "fault"], rows)
    fault_acks = sum(r["end_id"] - r["first_id"] for r in acknowledgements(["fault"]))
    if (
        recovered["rows"] != mid
        or fault_acks < 2 * batch
        or recovered["missing_by_phase"]["seed"]
        or recovered["missing_by_phase"]["live"]
        or recovered["acknowledged_missing"] != fault_acks
    ):
        raise RuntimeError("unexpected loss outside the deliberately unreplicated tail")
    producers = spawn("standby", rows, rows + 10 * batch, "promoted", 0)
    join(producers)
    rto = time.monotonic() - fault
    first_write = (
        min(r["ack_monotonic"] for r in acknowledgements(["promoted"])) - fault
    )
    after = oracle("standby", ["promoted"], rows + 10 * batch)
    if after["acknowledged_missing"] or after["rows"] != mid + 10 * batch:
        raise RuntimeError("promoted writes failed")
    latencies = [
        r["seconds"] for r in acknowledgements(["seed", "live", "fault", "promoted"])
    ]
    latencies.sort()
    save(
        "replica-test",
        {
            "configured_rows": rows,
            "quack_revision": revision,
            "clients": integer("CLIENTS", 4),
            "batch_rows": batch,
            "baseline": first,
            "full_replication": full,
            "baseline_clone": clone,
            "live_replication": incremental,
            "live_clone": live,
            "interrupted_replication": interrupted,
            "resumed_replication": resumed,
            "last_complete_clone": last,
            "promoted": recovered,
            "after_promoted_writes": after,
            "recovery_and_ten_batches_seconds": rto,
            "first_recovered_write_seconds": first_write,
            "fault_client_errors": [
                json.loads(p.read_text()) for p in RESULTS.glob("fault-*-errors.json")
            ],
            "batch_p50_seconds": latencies[len(latencies) // 2],
            "batch_p99_seconds": latencies[
                min(len(latencies) - 1, int(len(latencies) * 0.99))
            ],
            "total_seconds": time.monotonic() - start,
        },
    )


def main():
    action = sys.argv[1]
    if action == "up":
        provision()
    elif action == "disk-test":
        disk_test()
    elif action == "crash-start":
        crash_start()
    elif action == "crash-finish":
        crash_finish()
    elif action == "benchmark":
        benchmark()
    elif action == "replica-test":
        replica_test()
    elif action == "status":
        for node in NODES:
            try:
                print(
                    node,
                    ssh(
                        node,
                        "zpool status -x; zfs list -t filesystem,snapshot; systemctl is-active ducklab.service || true",
                    ),
                )
            except Exception as e:
                print(node, str(e))
    elif action == "down":
        # A power-cut primary container is already down; stop any reachable owner.
        for node in NODES:
            try:
                ssh(node, "systemctl stop ducklab.service", timeout=15)
            except (RuntimeError, subprocess.TimeoutExpired):
                pass
    else:
        raise ValueError(
            "usage: lab.py up|disk-test|crash-start|crash-finish|replica-test|benchmark|status|down"
        )


if __name__ == "__main__":
    main()
