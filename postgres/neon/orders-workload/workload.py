"""Native Neon APIs and PostgreSQL COPY; external commits are never guessed."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit
import sys
import time
import urllib.request
import uuid

import psycopg
from recovery import native_wal, recover_wal, verify_recovery

ROOT = Path("/results")
DSNS = {"neon": os.environ["NEON_DSN"], "postgres": os.environ["PG_DSN"]}
COUNT = int(os.environ.get("ROWS", "1000000"))
PG_REPLICAS = int(os.environ.get("PG_REPLICAS", "2"))
if PG_REPLICAS not in (0, 2):
    raise ValueError("PG_REPLICAS must be 0 or 2")
if COUNT < 1:
    raise ValueError("ROWS must be positive")


def save(name, value):
    (ROOT / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")


def api(path, body=None, method="GET"):
    request = urllib.request.Request(
        "http://pageserver:9898/v1/" + path,
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        data = response.read()
        return json.loads(data) if data else None


def config(ids, timeline, filename):
    spec = json.loads(Path("/config/compute-template.json").read_text())
    settings = spec["spec"]["cluster"]["settings"]
    for row in settings:
        if row["name"] == "neon.tenant_id":
            row["value"] = ids["tenant"]
        if row["name"] == "neon.timeline_id":
            row["value"] = timeline
    path = ROOT / "generated" / filename
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(spec, indent=2) + "\n")


def init():
    marker = ROOT / "ids.json"
    if marker.exists():
        ids = json.loads(marker.read_text())
        # Never silently replace a missing tenant and pretend the old data survived.
        api("tenant/" + ids["tenant"])
    else:
        ids = {"tenant": uuid.uuid4().hex, "timeline": uuid.uuid4().hex}
        api(
            "tenant/" + ids["tenant"] + "/location_config",
            {"mode": "AttachedSingle", "generation": 1, "tenant_conf": {}},
            "PUT",
        )
        api(
            "tenant/" + ids["tenant"] + "/timeline",
            {"new_timeline_id": ids["timeline"], "pg_version": 17},
            "POST",
        )
        save("ids", ids)
    config(ids, ids["timeline"], "compute.json")


def payload(i):
    return hashlib.sha256(f"order:{i}".encode()).digest()


def verify_dsn(dsn, count=COUNT):
    seen = bytearray(count)
    total = 0
    with psycopg.connect(dsn) as db:
        with db.cursor(name="full_readback") as cur:
            cur.itersize = 10000
            cur.execute("SELECT order_id,customer_id,amount,payload FROM orders")
            for i, customer, amount, body in cur:
                if (
                    i < 0
                    or i >= count
                    or seen[i]
                    or customer != i % 10000
                    or amount != (i * 17) % 100000
                    or bytes(body) != payload(i)
                ):
                    raise RuntimeError(f"wrong/duplicate order {i}")
                seen[i] = 1
                total += 1
    if total != count or not all(seen):
        raise RuntimeError(f"missing rows: {total}/{count}")
    return {
        "rows": total,
        "bytes_verified": total * 32,
        "missing": 0,
        "duplicate": 0,
        "wrong_values": 0,
    }


def verify():
    out = {system: verify_dsn(dsn) for system, dsn in DSNS.items()}
    for system, dsn in DSNS.items():
        with psycopg.connect(dsn) as db:
            settings = {
                name: db.execute("SHOW " + name).fetchone()[0]
                for name in (
                    "server_version",
                    "fsync",
                    "synchronous_commit",
                    "synchronous_standby_names",
                )
            }
            if settings["fsync"] != "on" or settings["synchronous_commit"] != "on":
                raise RuntimeError(f"unsafe durability settings: {system}, {settings}")
            out[system]["settings"] = settings
    with psycopg.connect(DSNS["postgres"], autocommit=True) as db:
        rows = db.execute(
            "SELECT application_name,sync_state,state,replay_lsn FROM pg_stat_replication ORDER BY application_name"
        ).fetchall()
        if len(rows) != PG_REPLICAS or not all(
            row[1] == "quorum" and row[2] == "streaming" for row in rows
        ):
            raise RuntimeError(f"baseline quorum not ready: {rows}")
        target = db.execute("SELECT pg_current_wal_lsn()::text").fetchone()[0]
    for i in range(1, PG_REPLICAS + 1):
        dsn = f"postgresql://postgres:local-only-postgres@pg{i}:5432/postgres"
        end = time.monotonic() + 120
        with psycopg.connect(dsn, autocommit=True) as replica:
            while not replica.execute(
                "SELECT pg_last_wal_replay_lsn() >= %s::pg_lsn", (target,)
            ).fetchone()[0]:
                if time.monotonic() > end:
                    raise RuntimeError("replica replay timeout")
                time.sleep(0.5)
        out[f"postgres-replica{i}"] = verify_dsn(dsn)
    ids = json.loads((ROOT / "ids.json").read_text())
    out["native_timeline"] = api(f"tenant/{ids['tenant']}/timeline/{ids['timeline']}")
    save("correctness", out)
    if list(ROOT.glob("fault-*.jsonl")):
        verify_recovery()


def wait_baseline():
    end = time.monotonic() + 180
    with psycopg.connect(DSNS["postgres"], autocommit=True) as db:
        while time.monotonic() < end:
            actual = db.execute("SHOW synchronous_standby_names").fetchone()[0]
            rows = db.execute(
                "SELECT application_name,sync_state,state FROM pg_stat_replication ORDER BY application_name"
            ).fetchall()
            if PG_REPLICAS == 0 and actual == "" and rows == []:
                return
            if (
                PG_REPLICAS == 2
                and actual == "ANY 1 (pg1,pg2)"
                and rows
                == [
                    ("pg1", "quorum", "streaming"),
                    ("pg2", "quorum", "streaming"),
                ]
            ):
                print(
                    "PostgreSQL: two streaming quorum candidates, one synchronous ACK required"
                )
                return
            time.sleep(1)
    raise RuntimeError(f"baseline replication did not converge: {actual}, {rows}")


def branch():
    ids = json.loads((ROOT / "ids.json").read_text())
    if "branch" in ids:
        raise RuntimeError("branch already exists")
    with psycopg.connect(DSNS["neon"], autocommit=True) as db:
        lsn = db.execute("SELECT pg_current_wal_flush_lsn()::text").fetchone()[0]
    ids["branch"] = uuid.uuid4().hex
    started_epoch = time.time()
    start = time.monotonic()
    api(
        f"tenant/{ids['tenant']}/timeline",
        {
            "new_timeline_id": ids["branch"],
            "ancestor_timeline_id": ids["timeline"],
            "ancestor_start_lsn": lsn,
        },
        "POST",
    )
    save(
        "branch-create",
        {
            "seconds": time.monotonic() - start,
            "ancestor_lsn": lsn,
            "started_epoch": started_epoch,
        },
    )
    save("ids", ids)
    config(ids, ids["branch"], "branch.json")


def database_dsn(dsn, name):
    parts = urlsplit(dsn)
    return urlunsplit(
        (parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment)
    )


def copy_postgres_branch():
    # Explicit full logical copy baseline. It is not native copy-on-write or a
    # claim that restoring a database is equivalent to creating a Neon timeline.
    dsn = DSNS["postgres"]
    with psycopg.connect(database_dsn(dsn, "template1"), autocommit=True) as db:
        db.execute("CREATE DATABASE branch_copy")
    start = time.monotonic()
    dump = ROOT / "generated" / "postgres-branch.dump"
    subprocess.run(
        ["pg_dump", "--format=custom", "--dbname", dsn, "--file", str(dump)], check=True
    )
    subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--dbname",
            database_dsn(dsn, "branch_copy"),
            str(dump),
        ],
        check=True,
    )
    save(
        "postgres-branch-copy",
        {
            "seconds": time.monotonic() - start,
            "dump_bytes": dump.stat().st_size,
            "scope": "full logical pg_dump + pg_restore; database creation excluded",
        },
    )


def verify_branch():
    dsn = os.environ["BRANCH_DSN"]
    with psycopg.connect(dsn) as db:
        if db.execute("SELECT amount FROM orders WHERE order_id=0").fetchone() != (0,):
            raise RuntimeError("branch did not serve the expected parent row")
    creation = json.loads((ROOT / "branch-create.json").read_text())
    creation["seconds_until_usable_sql"] = time.time() - creation["started_epoch"]
    save("branch-create", creation)
    out = verify_dsn(dsn)
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE orders SET amount=amount+1 WHERE order_id=0")
    with psycopg.connect(DSNS["neon"]) as parent:
        if (
            parent.execute("SELECT amount FROM orders WHERE order_id=0").fetchone()[0]
            != 0
        ):
            raise RuntimeError("branch changed parent")
    with psycopg.connect(dsn) as db:
        db.execute("UPDATE orders SET amount=amount-1 WHERE order_id=0")
    out["parent_isolation"] = True
    save("branch-correctness", out)
    copy_postgres_branch()
    save(
        "postgres-branch-correctness",
        verify_dsn(database_dsn(DSNS["postgres"], "branch_copy")),
    )


def drop():
    for dsn in DSNS.values():
        with psycopg.connect(dsn) as db:
            db.execute("DROP TABLE IF EXISTS orders")
            db.execute("DROP TABLE IF EXISTS recovery_events")
    ids = json.loads((ROOT / "ids.json").read_text())
    if "branch" in ids:
        with psycopg.connect(os.environ["BRANCH_DSN"]) as db:
            db.execute("DROP TABLE IF EXISTS orders")
    with psycopg.connect(
        database_dsn(DSNS["postgres"], "template1"), autocommit=True
    ) as db:
        db.execute("DROP DATABASE IF EXISTS branch_copy")
    save("cleanup", {"workload_tables_dropped": True, "infrastructure_retained": True})


ROOT.mkdir(exist_ok=True)
if __name__ == "__main__":
    commands = {
        "init": init,
        "verify": verify,
        "wait-baseline": wait_baseline,
        "branch": branch,
        "verify-branch": verify_branch,
        "drop": drop,
        "verify-recovery": verify_recovery,
        "wal-status": lambda: print(json.dumps(native_wal())),
        "recover-wal": recover_wal,
    }
    commands[sys.argv[1]]()
