"""Range-query benchmark for /Root/testdb (the `range` part of bench/extended.sh).

Runs in the `bench-range` compose service (uv image, BENCH client CPUs) with the official
`ydb` Python SDK over the Query Service:

1. creates `range_bench` (PK tenant, ts, id; split at tenant boundaries) and bulk-loads
   RANGE_ROWS rows with BulkUpsert from RANGE_LOAD_PROCS processes (timed, rows/s),
2. adds the GLOBAL SYNC index idx_category ON (category, ts) (timed build),
3. for each workload x RANGE_THREADS, runs parameterized queries with random parameters for
   RANGE_TIME seconds (after RANGE_WARMUP s not counted) in serializable read-write
   transactions: PK range scans of 10/100/1,000 rows, the same through the index, and
   COUNT/SUM over a ~10,000-row PK range,
4. streams the whole table with 1 and RANGE_PROCS parallel readers (rows/s),
5. drops the table.

Concurrency comes from min(threads, RANGE_PROCS) processes, each with its own Driver and
QuerySessionPool and threads/procs worker threads; process i seeds the driver with dynamic
node i % 2 and SDK discovery spreads sessions over both. Writes a JSON summary to RANGE_OUT
and prints a table.
"""

import json
import multiprocessing as mp
import os
import random
import sys
import threading
import time

import ydb

ENDPOINTS = os.environ.get("YDB_ENDPOINTS", "grpc://ydb-dynamic-1:2136,grpc://ydb-dynamic-2:2136").split(",")
DATABASE = os.environ.get("YDB_DATABASE", "/Root/testdb")
SMOKE = bool(os.environ.get("SMOKE"))


def env_int(name, default):
    v = os.environ.get(name, "")
    return int(v) if v else default


ROWS = env_int("RANGE_ROWS", 100_000 if SMOKE else 1_000_000)
PER_TENANT = env_int("RANGE_ROWS_PER_TENANT", 20_000)
TIME = env_int("RANGE_TIME", 10 if SMOKE else 60)
WARMUP = env_int("RANGE_WARMUP", 2)
THREADS = [int(t) for t in (os.environ.get("RANGE_THREADS") or "1 4 16 64").split()]
PROCS = env_int("RANGE_PROCS", 4)
LOAD_PROCS = env_int("RANGE_LOAD_PROCS", 4)
BATCH = env_int("RANGE_BATCH", 2_000)
PARTITIONS = env_int("RANGE_PARTITIONS", 4)
AGG_ROWS = env_int("RANGE_AGG_ROWS", 10_000)
TIMEOUT = float(os.environ.get("RANGE_TIMEOUT_S") or 10)
OUT = os.environ.get("RANGE_OUT", "/results/.range.json")
WORKLOADS = (os.environ.get("RANGE_WORKLOADS") or "").split()  # empty: all

TABLE = "range_bench"
TENANTS = max(1, ROWS // PER_TENANT)
CATEGORIES = TENANTS  # ~PER_TENANT rows per category, ~1 per ts step across all tenants
TS0 = 1_700_000_000_000_000  # microseconds; row i of every tenant is at TS0 + i * STEP
STEP = 1_000
MASK = (1 << 64) - 1


def mix(x):  # splitmix64: deterministic pseudo-random category/amount/payload per row
    x = (x + 0x9E3779B97F4A7C15) & MASK
    x = ((x ^ (x >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    x = ((x ^ (x >> 27)) * 0x94D049BB133111EB) & MASK
    return x ^ (x >> 31)


def ts(i):
    return TS0 + i * STEP


def driver_for(i=0):
    d = ydb.Driver(endpoint=ENDPOINTS[i % len(ENDPOINTS)], database=DATABASE)
    d.wait(timeout=30, fail_fast=True)
    return d


# ------------------------------------------------------------------ schema and load
def ddl(pool):
    keys = sorted({TENANTS * k // PARTITIONS for k in range(1, PARTITIONS)} - {0})
    at_keys = f", PARTITION_AT_KEYS = ({', '.join(f'({k})' for k in keys)})" if keys else ""
    pool.execute_with_retries(f"DROP TABLE IF EXISTS {TABLE}")
    pool.execute_with_retries(f"""
        CREATE TABLE {TABLE} (
            tenant Uint32 NOT NULL,
            ts Uint64 NOT NULL,
            id Uint64 NOT NULL,
            category Uint32,
            amount Uint64,
            payload String,
            PRIMARY KEY (tenant, ts, id)
        ) WITH (AUTO_PARTITIONING_MIN_PARTITIONS_COUNT = {len(keys) + 1}{at_keys})""")
    return len(keys) + 1


COLUMNS = (
    ydb.BulkUpsertColumns()
    .add_column("tenant", ydb.PrimitiveType.Uint32)
    .add_column("ts", ydb.PrimitiveType.Uint64)
    .add_column("id", ydb.PrimitiveType.Uint64)
    .add_column("category", ydb.OptionalType(ydb.PrimitiveType.Uint32))
    .add_column("amount", ydb.OptionalType(ydb.PrimitiveType.Uint64))
    .add_column("payload", ydb.OptionalType(ydb.PrimitiveType.String))
)


def load_tenants(args):
    """BulkUpsert every row of the given tenants in BATCH-row requests; returns rows written."""
    proc, tenants = args
    driver = driver_for(proc)
    path = f"{DATABASE}/{TABLE}"
    settings = ydb.RetrySettings(idempotent=True)
    n = 0
    try:
        for t in tenants:
            for start in range(0, PER_TENANT, BATCH):
                rows = []
                for i in range(start, min(start + BATCH, PER_TENANT)):
                    rid = t * PER_TENANT + i
                    h = mix(rid)
                    rows.append({"tenant": t, "ts": ts(i), "id": rid, "category": h % CATEGORIES,
                                 "amount": (h >> 20) % 1000, "payload": (b"%016x" % h) * 6})
                ydb.retry_operation_sync(lambda: driver.table_client.bulk_upsert(path, rows, COLUMNS), settings)
                n += len(rows)
    finally:
        driver.stop()
    return n


# ------------------------------------------------------------------ workloads
SELECT = "SELECT tenant, ts, id, category, amount, payload"


def workloads():
    """name -> (query text, params(rng) -> dict, rows scanned per query or None = rows returned)"""
    w = {}
    for n in (10, 100, 1000):
        w[f"pk range {n}"] = (
            f"""DECLARE $t AS Uint32; DECLARE $a AS Uint64; DECLARE $b AS Uint64;
            {SELECT} FROM {TABLE} WHERE tenant = $t AND ts BETWEEN $a AND $b ORDER BY ts LIMIT {n};""",
            lambda r, n=n: pk_params(r, n), None)
    for n in (10, 100, 1000):
        # the index is (category, ts): about 1 row of a category per ts step, so a window of 2n
        # steps holds ~2n matches and LIMIT n returns n (rows returned are counted anyway)
        w[f"index range {n}"] = (
            f"""DECLARE $c AS Uint32; DECLARE $a AS Uint64; DECLARE $b AS Uint64;
            {SELECT} FROM {TABLE} VIEW idx_category
            WHERE category = $c AND ts BETWEEN $a AND $b ORDER BY ts LIMIT {n};""",
            lambda r, n=n: idx_params(r, 2 * n), None)
    agg = min(AGG_ROWS, PER_TENANT)
    w[f"pk agg {agg}"] = (
        f"""DECLARE $t AS Uint32; DECLARE $a AS Uint64; DECLARE $b AS Uint64;
        SELECT COUNT(*) AS n, SUM(amount) AS total FROM {TABLE} WHERE tenant = $t AND ts BETWEEN $a AND $b;""",
        lambda r: pk_params(r, agg), agg)
    if WORKLOADS:
        w = {k: v for k, v in w.items() if k.replace(" ", "-") in WORKLOADS or k.split()[0] in WORKLOADS}
    return w


def window(r, n):
    s = r.randrange(0, PER_TENANT - n + 1)
    return ydb.TypedValue(ts(s), ydb.PrimitiveType.Uint64), ydb.TypedValue(ts(s + n - 1), ydb.PrimitiveType.Uint64)


def pk_params(r, n):
    a, b = window(r, n)
    return {"$t": ydb.TypedValue(r.randrange(TENANTS), ydb.PrimitiveType.Uint32), "$a": a, "$b": b}


def idx_params(r, n):
    a, b = window(r, min(n, PER_TENANT))
    return {"$c": ydb.TypedValue(r.randrange(CATEGORIES), ydb.PrimitiveType.Uint32), "$a": a, "$b": b}


def run_query(pool, query, params, calls):
    """one serializable read-write transaction with retries; returns rows returned"""

    def callee(session):
        calls[0] += 1
        rows = 0
        with session.transaction(ydb.QuerySerializableReadWrite()).execute(
                query, params, commit_tx=True, settings=ydb.BaseRequestSettings().with_timeout(TIMEOUT)) as it:
            for rs in it:
                rows += len(rs.rows)
        return rows

    return pool.retry_operation_sync(callee, ydb.RetrySettings(idempotent=True, max_retries=5))


def worker_proc(proc, tasks, results):
    """long-lived load process: one Driver + QuerySessionPool, a task = (name, threads, start_at)"""
    driver = driver_for(proc)
    pool = ydb.QuerySessionPool(driver, size=max(THREADS))
    wl = workloads()
    try:
        while (task := tasks.get()) is not None:
            name, nthreads, start_at = task
            query, params, _ = wl[name]
            measure_from, end = start_at + WARMUP, start_at + WARMUP + TIME
            out = [{"n": 0, "rows": 0, "retries": 0, "errors": 0, "lat": [], "err": []} for _ in range(nthreads)]

            def loop(o, seed):
                r = random.Random(seed)
                while (now := time.time()) < end:
                    calls = [0]
                    t0 = time.perf_counter()
                    try:
                        rows = run_query(pool, query, params(r), calls)
                        ok = True
                    except Exception as e:  # noqa: BLE001 - counted and sampled
                        ok, rows = False, 0
                        if len(o["err"]) < 3:
                            o["err"].append(f"{type(e).__name__}: {str(e)[:200]}")
                    ms = (time.perf_counter() - t0) * 1000
                    if now >= measure_from and time.time() <= end + TIMEOUT:
                        o["retries"] += max(0, calls[0] - 1)
                        if ok:
                            o["n"] += 1
                            o["rows"] += rows
                            o["lat"].append(ms)
                        else:
                            o["errors"] += 1

            time.sleep(max(0, start_at - time.time()))
            ts_ = [threading.Thread(target=loop, args=(o, hash((proc, name, i, start_at)))) for i, o in enumerate(out)]
            for t in ts_:
                t.start()
            for t in ts_:
                t.join()
            results.put({k: sum(o[k] for o in out) for k in ("n", "rows", "retries", "errors")}
                        | {"lat": [x for o in out for x in o["lat"]], "err": [x for o in out for x in o["err"]][:3]})
    finally:
        pool.stop()
        driver.stop()


def pct(sorted_ms, q):
    return round(sorted_ms[int(q * (len(sorted_ms) - 1))], 2) if sorted_ms else None


# ------------------------------------------------------------------ full scan
def scan_part(args):
    """stream every row of tenants [lo, hi) in one query; returns (rows, seconds)"""
    proc, lo, hi = args
    driver = driver_for(proc)
    pool = ydb.QuerySessionPool(driver, size=1)
    q = f"""DECLARE $lo AS Uint32; DECLARE $hi AS Uint32;
        {SELECT} FROM {TABLE} WHERE tenant >= $lo AND tenant < $hi;"""
    p = {"$lo": ydb.TypedValue(lo, ydb.PrimitiveType.Uint32), "$hi": ydb.TypedValue(hi, ydb.PrimitiveType.Uint32)}
    try:
        t0 = time.perf_counter()
        rows = pool.retry_operation_sync(lambda s: sum(len(rs.rows) for rs in s.transaction(
            ydb.QuerySerializableReadWrite()).execute(q, p, commit_tx=True)), ydb.RetrySettings(idempotent=True))
        return rows, time.perf_counter() - t0
    finally:
        pool.stop()
        driver.stop()


def main():
    ctx = mp.get_context("spawn")  # no gRPC state shared across fork
    driver = driver_for(0)
    pool = ydb.QuerySessionPool(driver, size=4)
    report = {"tool": f"ydb Python SDK {ydb.__version__}",
              "table": {"name": TABLE, "rows": TENANTS * PER_TENANT, "tenants": TENANTS,
                        "rows_per_tenant": PER_TENANT, "categories": CATEGORIES,
                        "primary_key": "(tenant, ts, id)", "index": "idx_category GLOBAL SYNC ON (category, ts)",
                        "payload_bytes": 96},
              "parameters": {"time_s": TIME, "warmup_s": WARMUP, "threads": THREADS, "procs": PROCS,
                             "load_procs": LOAD_PROCS, "batch_rows": BATCH, "tx_mode": "serializable_read_write",
                             "request_timeout_s": TIMEOUT, "max_retries": 5, "endpoints": ENDPOINTS}}
    try:
        report["table"]["partitions_at_create"] = ddl(pool)
        print(f"load: {TENANTS * PER_TENANT} rows ({TENANTS} tenants x {PER_TENANT}) with BulkUpsert, "
              f"{LOAD_PROCS} processes, {BATCH} rows per request", flush=True)
        chunks = [(p, list(range(p, TENANTS, LOAD_PROCS))) for p in range(min(LOAD_PROCS, TENANTS))]
        t0 = time.perf_counter()
        with ctx.Pool(len(chunks)) as lp:
            loaded = sum(lp.map(load_tenants, chunks))
        load_s = time.perf_counter() - t0
        report["load"] = {"rows": loaded, "seconds": round(load_s, 1), "rows_per_sec": round(loaded / load_s)}
        print(f"  {loaded} rows in {load_s:.1f}s = {loaded / load_s:,.0f} rows/s", flush=True)

        t0 = time.perf_counter()
        pool.execute_with_retries(f"ALTER TABLE {TABLE} ADD INDEX idx_category GLOBAL SYNC ON (category, ts)")
        # the build is a long operation: poll until the index answers
        deadline = time.time() + 1800
        while True:
            try:
                pool.execute_with_retries(f"SELECT id FROM {TABLE} VIEW idx_category WHERE category = 0 LIMIT 1")
                break
            except ydb.Error:
                if time.time() > deadline:
                    raise
                time.sleep(2)
        report["index_build_s"] = round(time.perf_counter() - t0, 1)
        print(f"index idx_category built in {report['index_build_s']}s", flush=True)

        wl = workloads()
        results, table = [], []
        nprocs = min(PROCS, max(THREADS))
        tasks = [ctx.Queue() for _ in range(nprocs)]
        res_q = ctx.Queue()
        procs = [ctx.Process(target=worker_proc, args=(i, tasks[i], res_q)) for i in range(nprocs)]
        for p in procs:
            p.start()
        try:
            for name, (_, _, scanned) in wl.items():
                for threads in THREADS:
                    used = min(nprocs, threads)
                    split = [threads // used + (i < threads % used) for i in range(used)]
                    start_at = time.time() + 2
                    print(f"run: {name}, {threads} threads over {used} processes, {TIME}s", flush=True)
                    for i, n in enumerate(split):
                        tasks[i].put((name, n, start_at))
                    parts = [res_q.get() for _ in split]
                    lat = sorted(x for p in parts for x in p["lat"])
                    n = sum(p["n"] for p in parts)
                    rows = sum(p["rows"] for p in parts)
                    r = {"workload": name, "threads": threads, "queries": n, "qps": round(n / TIME, 1),
                         "rows_returned": rows,
                         "rows_per_sec": round((n * scanned if scanned else rows) / TIME),
                         "rows_per_query": round(rows / n, 1) if n else None,
                         "retries": sum(p["retries"] for p in parts), "errors": sum(p["errors"] for p in parts),
                         "p50_ms": pct(lat, 0.50), "p95_ms": pct(lat, 0.95), "p99_ms": pct(lat, 0.99),
                         "max_ms": round(lat[-1], 2) if lat else None}
                    if scanned:
                        r["rows_aggregated_per_query"] = scanned
                    errs = [e for p in parts for e in p["err"]][:3]
                    if errs:
                        r["error_samples"] = errs
                        print("  errors: " + " | ".join(errs), flush=True)
                    results.append(r)
                    line = (f"{name:<18} {threads:>7} {r['qps']:>9,.0f} {r['rows_per_sec']:>11,} {r['retries']:>7} "
                            f"{r['errors']:>6} {r['p50_ms'] or 0:>7.2f} {r['p95_ms'] or 0:>7.2f} {r['p99_ms'] or 0:>7.2f}")
                    table.append(line)
                    print("  " + line, flush=True)
        finally:
            for q in tasks:
                q.put(None)
            for p in procs:
                p.join(timeout=60)
        report["results"] = results

        scans = []
        for streams in sorted({1, min(PROCS, TENANTS)}):
            bounds = [TENANTS * k // streams for k in range(streams + 1)]
            args = [(i, bounds[i], bounds[i + 1]) for i in range(streams)]
            print(f"scan: full table, {streams} parallel stream(s)", flush=True)
            t0 = time.perf_counter()
            with ctx.Pool(streams) as sp:
                got = sp.map(scan_part, args)
            wall = time.perf_counter() - t0
            rows = sum(g[0] for g in got)
            scans.append({"streams": streams, "rows": rows, "seconds": round(wall, 2), "rows_per_sec": round(rows / wall)})
            print(f"  {rows} rows in {wall:.2f}s = {rows / wall:,.0f} rows/s", flush=True)
        report["scan"] = scans

        print()
        print(f"{'workload':<18} {'threads':>7} {'queries/s':>9} {'rows/s':>11} {'retries':>7} {'errors':>6} "
              f"{'p50 ms':>7} {'p95 ms':>7} {'p99 ms':>7}")
        print("\n".join(table))
        print(f"load {report['load']['rows_per_sec']:,} rows/s ({report['load']['seconds']}s); "
              f"index build {report['index_build_s']}s; full scan " +
              ", ".join(f"{s['streams']} stream(s) {s['rows_per_sec']:,} rows/s" for s in scans))
    finally:
        try:
            pool.execute_with_retries(f"DROP TABLE IF EXISTS {TABLE}")
            print(f"dropped {TABLE}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"could not drop {TABLE}: {e}", file=sys.stderr)
        pool.stop()
        driver.stop()
        with open(OUT, "w") as f:
            json.dump(report, f, indent=1)


if __name__ == "__main__":
    main()
