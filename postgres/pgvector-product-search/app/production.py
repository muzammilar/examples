"""Native PostgreSQL barriers and full-copy verification, separate from fault injection."""

import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

import numpy as np
import psycopg
from pgvector import Vector

from client import Adapter
from workload import Fixture, META, save, parameter, recall
import statistics

ROOT = Path("/results")
HOSTS = os.environ["PG_NODES"].split(",")


def connect(host):
    return psycopg.connect(
        host=host,
        user="postgres",
        password="local-postgres",
        dbname="postgres",
        connect_timeout=2,
        autocommit=True,
        options="-c statement_timeout=10000",
    )


def state():
    result = {}
    for host in HOSTS:
        try:
            with connect(host) as c:
                replica = c.execute("SELECT pg_is_in_recovery()").fetchone()[0]
                row = {
                    "role": "replica" if replica else "primary",
                    "postgres_version": c.execute("SHOW server_version").fetchone()[0],
                    "pgvector_version": c.execute(
                        "SELECT extversion FROM pg_extension WHERE extname='vector'"
                    ).fetchone(),
                    "system_identifier": str(
                        c.execute(
                            "SELECT system_identifier FROM pg_control_system()"
                        ).fetchone()[0]
                    ),
                    "postmaster_started": str(
                        c.execute("SELECT pg_postmaster_start_time()").fetchone()[0]
                    ),
                    "timeline": c.execute(
                        "SELECT timeline_id FROM pg_control_checkpoint()"
                    ).fetchone()[0],
                }
                if replica:
                    row["replay_lsn"] = c.execute(
                        "SELECT pg_last_wal_replay_lsn()::text"
                    ).fetchone()[0]
                    row["receiver"] = [
                        dict(zip(["status", "timeline", "sender"], r))
                        for r in c.execute(
                            "SELECT status,received_tli,sender_host FROM pg_stat_wal_receiver"
                        )
                    ]
                else:
                    row["flush_lsn"] = c.execute(
                        "SELECT pg_current_wal_flush_lsn()::text"
                    ).fetchone()[0]
                    row["replication"] = [
                        dict(zip(["name", "state", "sync_state", "replay_lsn"], r))
                        for r in c.execute(
                            "SELECT application_name,state,sync_state,replay_lsn::text FROM pg_stat_replication"
                        )
                    ]
                    if len(HOSTS) > 1:
                        with urllib.request.urlopen(
                            "http://" + host + ":8008/config", timeout=3
                        ) as response:
                            row["patroni_config"] = json.load(response)
                    row["settings"] = dict(
                        c.execute(
                            "SELECT name,setting FROM pg_settings WHERE name IN ('fsync','synchronous_commit','synchronous_standby_names','full_page_writes','data_checksums')"
                        )
                    )
                result[host] = row
        except (psycopg.Error, OSError, ValueError) as err:
            result[host] = {"error": str(err)}
    return result


def primary(states):
    candidates = [
        host for host, value in states.items() if value.get("role") == "primary"
    ]
    if len(candidates) != 1:
        raise RuntimeError("expected exactly one primary: " + repr(states))
    return candidates[0]


def lsn(value):
    if not value:
        return -1
    high, low = value.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def healthy(states, fence=None, owner=None, members=None):
    members = members or HOSTS
    leader = primary(states)
    if owner is not None and leader != owner:
        raise RuntimeError("primary changed during verification")
    p = states[leader]
    settings = p["settings"]
    if any(
        settings.get(k) != "on" for k in ["fsync", "full_page_writes", "data_checksums"]
    ):
        raise RuntimeError("unsafe PostgreSQL durability settings")
    if settings.get("synchronous_commit") != "on":
        raise RuntimeError("synchronous commit disabled")
    if len(HOSTS) > 1:
        config = p["patroni_config"]
        expected = {
            "synchronous_mode": True,
            "synchronous_mode_strict": True,
            "synchronous_node_count": 1,
            "check_timeline": True,
            "maximum_lag_on_failover": 0,
        }
        if any(config.get(key) != value for key, value in expected.items()):
            raise RuntimeError("unsafe Patroni failover configuration")
    if len(members) > 1:
        peers = {r["name"]: r for r in p["replication"]}
        if set(peers) != set(members) - {leader} or not any(
            r["sync_state"] == "sync" for r in peers.values()
        ):
            raise RuntimeError("native replication/synchronous membership incomplete")
        for host in set(members) - {leader}:
            row = states[host]
            if (
                row.get("role") != "replica"
                or row["system_identifier"] != p["system_identifier"]
            ):
                raise RuntimeError("missing/wrong replica system identity")
            if (
                peers[host]["state"] != "streaming"
                or len(row["receiver"]) != 1
                or row["receiver"][0]["status"] != "streaming"
            ):
                raise RuntimeError("replica not streaming")
            if row["receiver"][0]["timeline"] != p["timeline"]:
                raise RuntimeError("replica receiving different timeline")
            if fence is not None and (
                lsn(row["replay_lsn"]) < lsn(fence)
                or lsn(peers[host]["replay_lsn"]) < lsn(fence)
            ):
                raise RuntimeError("replica has not replayed verification fence")
    return leader


def wait_healthy(fence=None, owner=None, members=None):
    deadline = time.monotonic() + 240
    requested_fence = fence
    captured_owner = owner
    captured_timeline = None
    captured_at = time.time()
    last = None
    while time.monotonic() < deadline:
        last = state()
        try:
            leader = healthy(last, requested_fence, owner, members)
            if requested_fence is None:
                timeline = last[leader]["timeline"]
                if captured_owner != leader or captured_timeline != timeline:
                    captured_owner = leader
                    captured_timeline = timeline
                    fence = last[leader]["flush_lsn"]
                    captured_at = time.time()
                healthy(last, fence, captured_owner, members)
            last[leader]["recovery_barrier"] = {
                "flush_lsn": fence,
                "timeline": last[leader]["timeline"],
                "captured_at": captured_at,
                "confirmed_at": time.time(),
                "scope": "all required standbys and primary-reported replay positions reached the captured WAL fence; caller traffic need not stop",
                "verifier_source_sha256": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
            }
            return last
        except (RuntimeError, KeyError):
            time.sleep(1)
    save("native-timeout-" + str(time.time_ns()), last)
    raise RuntimeError("native recovery timed out: " + repr(last))


def histories(active_case=None):
    intents, acknowledgements = {}, {}
    counts = {"attempted_rows": 0, "acknowledged_rows": 0, "ambiguous_rows": 0}
    files = sorted(ROOT.glob("cases/*/writer.jsonl"))
    for file in files:
        requests = {}
        with file.open() as stream:
            for line in stream:
                if not line.endswith("\n") and file.parent.name == active_case:
                    break
                event = json.loads(line)
                stage, rows, request = (
                    event["stage"],
                    event["rows"],
                    event["request_id"],
                )
                if not isinstance(rows, list) or not rows:
                    raise RuntimeError("empty/invalid witnessed mutation")
                if stage == "attempt":
                    if request in requests:
                        raise RuntimeError("duplicate witness intent")
                    requests[request] = rows
                    counts["attempted_rows"] += len(rows)
                    for row in rows:
                        key = (row["id"], row["version"])
                        if key in intents:
                            raise RuntimeError("reused operation identity")
                        intents[key] = row
                elif stage in ("ack", "ambiguous"):
                    if requests.get(request) != rows:
                        raise RuntimeError("outcome without identical intent")
                    # A process can die after COMMIT but before journaling any outcome.
                    requests.pop(request)
                    counts[
                        "acknowledged_rows" if stage == "ack" else "ambiguous_rows"
                    ] += len(rows)
                    if stage == "ack":
                        for row in rows:
                            acknowledgements[row["id"]] = max(
                                acknowledgements.get(row["id"], 0), row["version"]
                            )
                else:
                    raise RuntimeError("unknown writer event")
        counts["ambiguous_rows"] += sum(len(rows) for rows in requests.values())
    return intents, acknowledgements, counts, files


def check_row(fixture, pid, data, tenant, stock, price, version, vector, intents, acks):
    metadata = [
        k for k in META if k not in ["id", "tenant", "stock", "price_cents", "version"]
    ]
    expected = fixture.row(pid, version)
    actual = dict(
        data,
        id=str(pid),
        tenant=tenant,
        stock=stock,
        price_cents=price,
        version=version,
    )
    if actual != expected or set(data) != set(metadata):
        raise RuntimeError("wrong metadata/version body: " + str(pid))
    if version < acks.get(str(pid), 0):
        raise RuntimeError("ACKNOWLEDGED DATA LOSS: " + str(pid))
    if version > 0:
        mutable = {
            k: actual[k] for k in ["id", "tenant", "stock", "price_cents", "version"]
        }
        if intents.get((str(pid), version)) != mutable:
            raise RuntimeError("unwitnessed applied version")
    value = np.asarray(
        vector.to_numpy() if isinstance(vector, Vector) else vector, dtype="<f4"
    )
    expected_vector = np.asarray(fixture.vectors[pid], dtype="<f4")
    if value.shape != (384,) or not np.array_equal(
        value.view("<u4"), expected_vector.view("<u4")
    ):
        raise RuntimeError("vector bit corruption: " + str(pid))
    return actual, value


def writer_quiescence():
    deadline = time.monotonic() + 30
    while True:
        sessions = {}
        for host in HOSTS:
            with connect(host) as c:
                sessions[host] = list(
                    c.execute(
                        "SELECT pid,state,wait_event_type,wait_event FROM pg_stat_activity WHERE application_name='inventory-writer'"
                    )
                )
        if not any(sessions.values()):
            return sessions
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "writer backends still executing after client stop: " + repr(sessions)
            )
        time.sleep(1)


def verify(fixture, adapter, target=None):
    active_case = os.environ.get("ACTIVE_CASE") if target else None
    intents, acks, counts, files = histories(active_case)
    before = state() if target else wait_healthy()
    drained = writer_quiescence() if not target else None
    leader = primary(before)
    members = list({leader, target}) if target else HOSTS
    copies_to_check = [target] if target else HOSTS
    with connect(leader) as c:
        c.execute("CHECKPOINT")
        fence = c.execute("SELECT pg_current_wal_flush_lsn()::text").fetchone()[0]
    native = wait_healthy(fence, leader, members)
    tag = str(time.time_ns())
    copies = {}
    for host in copies_to_check:
        path = ROOT / ("copy-" + tag + "-" + host + ".jsonl.gz")
        applied_nonack = 0
        with adapter.connect(host) as c, gzip.open(path, "xt") as raw:
            sizes = c.execute(
                "SELECT (SELECT count(*) FROM products),(SELECT count(*) FROM inventory)"
            ).fetchone()
            if sizes != (len(fixture.products), len(fixture.products)):
                raise RuntimeError("table counts differ on " + host)
            with c.transaction():
                c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                with c.cursor(name="full_copy_" + host, binary=True) as cur:
                    cur.execute(
                        "SELECT p.id,p.data,i.tenant,i.stock,i.price_cents,i.version,p.embedding FROM products p JOIN inventory i USING(id) ORDER BY p.id"
                    )
                    count = 0
                    for pid, data, tenant, stock, price, version, vector in cur:
                        if pid != count:
                            raise RuntimeError(
                                "missing/duplicate/misordered product on " + host
                            )
                        if (
                            target
                            and version > 0
                            and (str(pid), version) not in intents
                        ):
                            # The repeatable-read snapshot can include an intent written after our initial witness read.
                            intents.update(histories(active_case)[0])
                        actual, value = check_row(
                            fixture,
                            pid,
                            data,
                            tenant,
                            stock,
                            price,
                            version,
                            vector,
                            intents,
                            acks,
                        )
                        if version > acks.get(str(pid), 0):
                            applied_nonack += 1
                        raw.write(
                            json.dumps(
                                dict(
                                    actual,
                                    vector_sha256=hashlib.sha256(
                                        value.tobytes()
                                    ).hexdigest(),
                                )
                            )
                            + "\n"
                        )
                        count += 1
                    if count != len(fixture.products):
                        raise RuntimeError("short full-copy read")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        copies[host] = {
            "rows": count,
            "vector_coordinates": count * 384,
            "observed_nonack_versions": applied_nonack,
            "raw": path.name,
            "raw_sha256": digest,
        }
    after = state()
    healthy(after, fence, leader, members)
    for host in copies_to_check:
        if native[host]["postmaster_started"] != after[host]["postmaster_started"]:
            raise RuntimeError("member restarted during full-copy verification")
    report = dict(
        counts,
        histories=len(files),
        distinct_acknowledged_ids=len(acks),
        acknowledged_loss=0,
        verifier_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        drained_writer_sessions=drained,
        acknowledgement_check="latest acknowledged version per product is a lower bound; later witnessed updates may supersede it; not an immutable audit log",
        corruption=0,
        copies=copies,
        scope="rebuilt-member-live-snapshot" if target else "all-native-copies",
        fence_lsn=fence,
        native_before=native,
        native_after=after,
    )
    save("verification-" + tag, report)
    return report


def password_rejected(error):
    # libpq connection failures may omit SQLSTATE; fail closed on the exact server auth error.
    return isinstance(error, psycopg.OperationalError) and (
        error.sqlstate == "28P01"
        or (
            error.sqlstate is None
            and 'password authentication failed for user "postgres"' in str(error)
        )
    )


def access_checks():
    denied, read_only = {}, {}
    native = wait_healthy()
    for host in HOSTS:
        with connect(host) as probe:
            if probe.execute("SELECT 1").fetchone() != (1,):
                raise RuntimeError("valid credential probe failed")
        try:
            with psycopg.connect(
                host=host,
                user="postgres",
                password="incorrect-lab-password",
                dbname="postgres",
                connect_timeout=2,
            ):
                raise RuntimeError("bad password was accepted")
        except psycopg.OperationalError as err:
            if not password_rejected(err):
                raise
            denied[host] = {"sqlstate": err.sqlstate, "message": str(err)}
        with connect(host) as probe:
            if probe.execute("SELECT 1").fetchone() != (1,):
                raise RuntimeError("credential recovery probe failed")
        if native[host]["role"] == "replica":
            with connect(host) as connection:
                try:
                    connection.execute("UPDATE inventory SET stock=stock WHERE id=-1")
                except psycopg.errors.ReadOnlySqlTransaction as err:
                    read_only[host] = err.sqlstate
                else:
                    raise RuntimeError("replica accepted a mutation statement")
    return {"bad_password_rejections": denied, "replica_write_rejections": read_only}


def query_checks(fixture, adapter):
    dense, filtered = [], []
    for index, query in enumerate(
        fixture.queries[: parameter("CHECK_QUERIES", 50, 1, 1000)]
    ):
        hits = adapter.search(
            query["text"], fixture.qvectors[index], mode="dense", k=10
        )
        ids = [hit["id"] for hit in hits]
        if (
            len(ids) != 10
            or len(set(ids)) != 10
            or any(not 0 <= int(pid) < len(fixture.products) for pid in ids)
        ):
            raise RuntimeError("short/duplicate/invalid dense result")
        dense.append(recall(fixture, index, ids))
        if index < 10:
            tenant = fixture.products[fixture.exact[index]["ids"][0]]["tenant"]
            eligible = np.array(
                [i for i, p in enumerate(fixture.products) if p["tenant"] == tenant],
                dtype=np.int64,
            )
            ids = [
                hit["id"]
                for hit in adapter.search(
                    query["text"],
                    fixture.qvectors[index],
                    mode="dense",
                    k=10,
                    tenant=tenant,
                )
            ]
            if (
                len(ids) != min(10, len(eligible))
                or len(set(ids)) != len(ids)
                or any(fixture.products[int(pid)]["tenant"] != tenant for pid in ids)
            ):
                raise RuntimeError("tenant filter leak/short result")
            filtered.append(recall(fixture, index, ids, eligible))
    return {
        "checked_dense_queries": len(dense),
        "dense_recall_at_10": statistics.mean(dense),
        "checked_filtered_queries": len(filtered),
        "filtered_recall_at_10": statistics.mean(filtered),
        "inventory_transaction": adapter.strength_test(),
        "access_checks": access_checks(),
    }


def main():
    action = sys.argv[1]
    if action == "state":
        result = state()
    elif action == "healthy":
        result = wait_healthy()
    else:
        fixture = Fixture()
        adapter = Adapter(fixture)
        if action == "up":
            leader = primary(wait_healthy())
            with connect(leader) as c:
                exists = c.execute(
                    "SELECT to_regclass('public.products') IS NOT NULL,to_regclass('public.inventory') IS NOT NULL,to_regclass('public.lab_fixture') IS NOT NULL"
                ).fetchone()
                if any(exists) and not all(exists):
                    raise RuntimeError(
                        "partial or legacy state; preserve it and diagnose before initialization"
                    )
                if all(exists):
                    expected = json.dumps(fixture.manifest, sort_keys=True)
                    stored = c.execute(
                        "SELECT manifest FROM lab_fixture WHERE id=1"
                    ).fetchone()
                    if not stored or json.dumps(stored[0], sort_keys=True) != expected:
                        raise RuntimeError(
                            "existing database belongs to another corpus"
                        )
                else:
                    if list(ROOT.glob("cases/*/writer.jsonl")):
                        raise RuntimeError(
                            "refusing to load empty database over existing write histories"
                        )
                    load = adapter.load()
                    with connect(leader) as target:
                        target.execute(
                            "CREATE TABLE lab_fixture(id integer PRIMARY KEY CHECK(id=1),manifest jsonb NOT NULL)"
                        )
                        target.execute(
                            "INSERT INTO lab_fixture VALUES(1,%s::jsonb)",
                            [json.dumps(fixture.manifest)],
                        )
                    save("load-" + str(time.time_ns()), load)
            result = verify(fixture, adapter)
        elif action == "verify-rebuilt":
            target = os.environ["VERIFY_HOST"]
            if target not in HOSTS:
                raise ValueError("unknown rebuilt host")
            result = verify(fixture, adapter, target)
        elif action == "verify":
            result = verify(fixture, adapter)
        elif action == "search-check":
            # Stock changes remain visible in the same SQL transaction; it rolls back.
            result = query_checks(fixture, adapter)
        elif action == "benchmark":
            from workload import benchmark

            benchmark(adapter, fixture)
            result = json.loads((ROOT / "benchmark.json").read_text())
            save("benchmark-" + str(time.time_ns()), result)
        else:
            raise ValueError(action)
    save("command-" + action, result)


if __name__ == "__main__":
    main()
