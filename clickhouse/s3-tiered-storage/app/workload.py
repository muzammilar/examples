"""Native SQL over the documented HTTP API, with bounded queries and external checks."""

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path("/results")
ROWS = int(os.environ.get("ROWS", "2000000"))
if not 200000 <= ROWS <= 10000000 or ROWS % 200000:
    raise ValueError(
        "ROWS must be 200000..10000000 in even pairs of 100000-row batches"
    )


def save(name, value):
    with tempfile.NamedTemporaryFile("w", dir=ROOT, delete=False) as f:
        json.dump(value, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
        name_tmp = f.name
    os.replace(name_tmp, ROOT / (name + ".json"))


def query(sql, node=0, settings=None):
    args = {
        "max_execution_time": 120,
        "max_rows_to_read": 20000000,
        "max_result_rows": 20000,
        "max_memory_usage": 1200000000,
        "max_threads": 2,
        "timeout_before_checking_execution_speed": 0,
    }
    args.update(settings or {})
    with urlopen(
        Request(
            f"http://ch{node}:8123/?" + urlencode(args),
            data=sql.encode(),
            headers={
                "X-ClickHouse-User": "default",
                "X-ClickHouse-Key": "local-only-clickhouse",
            },
        ),
        timeout=150,
    ) as r:
        text = r.read().decode()
    return json.loads(text)["data"] if text.strip() else None


DDL = """CREATE TABLE IF NOT EXISTS lab.events (
 event_id UInt64 COMMENT 'Unique deterministic event ID',
 tenant_id UInt16 COMMENT '10,000 tenants, event_id modulo 10000',
 event_time DateTime('UTC') COMMENT 'Two monthly retention partitions',
 event_type LowCardinality(String), amount UInt16, payload FixedString(32)
) ENGINE=ReplicatedMergeTree('/clickhouse/tables/01/events', '{replica}')
PARTITION BY toYYYYMM(event_time) ORDER BY (tenant_id,event_time,event_id)
SETTINGS storage_policy='tiered', allow_remote_fs_zero_copy_replication=0"""


def create(node):
    query("CREATE DATABASE IF NOT EXISTS lab", node)
    query(DDL, node)


def sync():
    for node in range(3):
        query("SYSTEM SYNC REPLICA lab.events", node)


def insert(start, count, stamp, node=0):
    query(
        f"INSERT INTO lab.events SELECT number, toUInt16(number%10000), toDateTime('{stamp}','UTC'), ['view','click','purchase','logout'][1+intDiv(number,10000)%4], toUInt16((number*17+intDiv(number,10000)*31)%10000), SHA256(toString(number)) FROM numbers({start},{count})",
        node=node,
        settings={
            "insert_quorum": 2,
            "insert_quorum_timeout": 60000,
            "insert_deduplicate": 1,
        },
    )


def checks(node, expected):
    result = query(
        f"""SELECT count() AS rows, uniqExact(event_id) AS unique_ids,
        sum(event_id) AS id_sum, sum(amount) AS amount_sum,
        countIf(tenant_id != event_id%10000 OR amount != (event_id*17+intDiv(event_id,10000)*31)%10000 OR payload != SHA256(toString(event_id)) OR event_type != ['view','click','purchase','logout'][1+intDiv(event_id,10000)%4]) AS invalid_rows
        FROM lab.events WHERE event_id < {ROWS} AND event_time >= toDateTime('2020-01-01') LIMIT 1 FORMAT JSON""",
        node,
    )[0]
    if any(int(result[k]) != int(v) for k, v in expected.items()):
        raise RuntimeError(f"replica {node}: {result} != {expected}")
    times = json.loads((ROOT / "seed.json").read_text())["timestamps"]
    invalid = query(
        f"SELECT countIf(event_time!=if(event_id<{ROWS // 2},toDateTime('{times['old']}','UTC'),toDateTime('{times['hot']}','UTC'))) AS invalid FROM lab.events WHERE event_id<{ROWS} AND event_time>=toDateTime('2020-01-01') LIMIT 1 FORMAT JSON",
        node,
    )[0]["invalid"]
    if int(invalid):
        raise RuntimeError("baseline timestamp/retention partition mismatch")
    return result


def baseline_expected(rows):
    return {
        "rows": rows,
        "unique_ids": rows,
        "id_sum": rows * (rows - 1) // 2,
        "amount_sum": rows // 10000 * 49995000,
        "invalid_rows": 0,
    }


def seed():
    for i in range(3):
        # Inspect the live namespace before creating this example's bounded table.
        query(
            "SELECT name FROM system.databases WHERE name NOT IN ('system','information_schema','INFORMATION_SCHEMA') LIMIT 100 FORMAT JSON",
            i,
        )
        create(i)
    if any(
        int(
            query("SELECT count() AS rows FROM lab.events LIMIT 1 FORMAT JSON", node)[
                0
            ]["rows"]
        )
        for node in range(3)
    ):
        raise RuntimeError("table already seeded; clean lifecycle required")
    from run_state import archive_oracles

    archive_oracles(
        ROOT,
        extra={
            "expected",
            "explain-hot",
            "explain-cold_uncached",
            "explain-cold_warm_cache",
            "reconciliation",
        },
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    old = (now - timedelta(days=62)).strftime("%Y-%m-%d %H:%M:%S")
    hot = now.strftime("%Y-%m-%d %H:%M:%S")
    start = time.perf_counter()
    for first in range(0, ROWS, 100000):
        insert(first, 100000, old if first < ROWS // 2 else hot)
    sync()
    elapsed = time.perf_counter() - start
    expected = baseline_expected(ROWS)
    save("expected", expected)
    save(
        "seed",
        {
            "rows": ROWS,
            "replicas": 3,
            "batches": ROWS // 100000,
            "rows_per_batch": 100000,
            "seconds": elapsed,
            "rows_per_second": ROWS / elapsed,
            "timestamps": {"old": old, "hot": hot},
            "scope": "server-generated INSERT SELECT, quorum=2; replication sync included; correctness checked outside timer",
        },
    )

    for i in range(3):
        checks(i, expected)


def test():
    sync()
    expected = json.loads((ROOT / "expected.json").read_text())
    for i in range(3):
        schema = query(
            "SELECT name,type,comment FROM system.columns WHERE database='lab' AND table='events' ORDER BY position LIMIT 100 FORMAT JSON",
            i,
        )
        save(f"schema-{i}", schema)
        checks(i, expected)
        query(
            "ALTER TABLE lab.events MODIFY TTL event_time + INTERVAL 1 MONTH TO VOLUME 'cold'",
            i,
        )
    start = time.monotonic()
    while time.monotonic() - start < 600:
        states = [
            query(
                "SELECT disk_name,sum(rows) AS rows FROM system.parts WHERE database='lab' AND table='events' AND active GROUP BY disk_name LIMIT 10 FORMAT JSON",
                i,
            )
            for i in range(3)
        ]
        if all(
            sum(int(r["rows"]) for r in rows if r["disk_name"] == "remote_cache")
            >= ROWS // 2
            for rows in states
        ):
            break
        time.sleep(5)
    else:
        raise RuntimeError("TTL move did not converge to S3 on all replicas")
    for i in range(3):
        checks(i, expected)
    check_parts()
    save(
        "correctness",
        {
            "replicas": 3,
            "rows_per_replica": ROWS,
            "missing_rows": 0,
            "duplicate_ids": 0,
            "invalid_rows": 0,
            "ttl_seconds": time.monotonic() - start,
            "storage": states,
        },
    )
    print((ROOT / "correctness.json").read_text(), flush=True)


def check_parts():
    for node in range(3):
        rows = query(
            "CHECK TABLE lab.events FORMAT JSON",
            node,
            {"check_query_single_value_result": 0},
        )
        if not rows or any(int(r["is_passed"]) != 1 or r["message"] for r in rows):
            raise RuntimeError(
                f"native part checksum check failed on replica {node}: {rows}"
            )


def benchmark():
    times = json.loads((ROOT / "seed.json").read_text())["timestamps"]
    results = {}
    for name, stamp, cache in [
        ("hot", times["hot"], 0),
        ("cold_uncached", times["old"], 0),
        ("cold_warm_cache", times["old"], 1),
    ]:
        sql = f"SELECT event_type, count(), sum(amount) FROM lab.events WHERE tenant_id=42 AND event_time=toDateTime('{stamp}','UTC') AND event_id<{ROWS} GROUP BY event_type LIMIT 10 FORMAT JSON"
        save(
            "explain-" + name,
            query(
                "EXPLAIN indexes=1 " + sql.removesuffix(" FORMAT JSON") + " FORMAT JSON"
            ),
        )
        query(
            sql,
            settings={
                "enable_filesystem_cache": cache,
                "use_query_cache": 0,
                "use_uncompressed_cache": 0,
            },
        )
        latencies = []
        for _ in range(int(os.environ.get("QUERY_RUNS", "20"))):
            begin = time.perf_counter()
            rows = query(
                sql,
                settings={
                    "enable_filesystem_cache": cache,
                    "use_query_cache": 0,
                    "use_uncompressed_cache": 0,
                },
            )
            first = 0 if name.startswith("cold") else ROWS // 2
            expected = {}
            for event_id in range(first + 42, first + ROWS // 2, 10000):
                kind = ["view", "click", "purchase", "logout"][(event_id // 10000) % 4]
                counts = expected.setdefault(kind, [0, 0])
                counts[0] += 1
                counts[1] += (event_id * 17 + (event_id // 10000) * 31) % 10000
            actual = {
                r["event_type"]: [int(r["count()"]), int(r["sum(amount)"])]
                for r in rows
            }
            if actual != expected:
                raise RuntimeError(f"wrong tenant aggregation: {actual} != {expected}")
            latencies.append(time.perf_counter() - begin)
        ordered = sorted(latencies)
        results[name] = {
            "runs": len(ordered),
            "qps": len(ordered) / sum(ordered),
            "p50_ms": 1000 * statistics.median(ordered),
            "p99_ms": 1000 * ordered[math.ceil(len(ordered) * 0.99) - 1],
        }
    save("benchmark", results)
    print(json.dumps(results, indent=2), flush=True)


if __name__ == "__main__":
    {
        "seed": seed,
        "test": test,
        "benchmark": benchmark,
        "drop": lambda: [
            query("DROP TABLE IF EXISTS lab.events SYNC", i) for i in range(3)
        ],
        "verify": lambda: [
            checks(i, json.loads((ROOT / "expected.json").read_text()))
            for i in range(3)
        ],
    }[sys.argv[1]]()
