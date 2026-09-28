"""FoundationDB benchmark: bulk load, random point reads, blind writes, read-modify-write
transfers on a small hot set (serializable conflicts + retries), the same hot set with
conflict-free atomic adds, and range reads. N client processes run each workload for
DURATION seconds. Run with `make benchmark [SMOKE=1]`."""
import json
import multiprocessing as mp
import os
import platform
import random
import struct
import time
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version

SMOKE = os.environ.get("SMOKE", "") not in ("", "0")
DURATION = float(os.environ.get("DURATION") or (5 if SMOKE else 30))  # seconds per workload
CLIENTS = int(os.environ.get("CLIENTS") or (4 if SMOKE else 8))  # client processes
ROWS = int(os.environ.get("ROWS") or (10_000 if SMOKE else 100_000))
VALUE = int(os.environ.get("VALUE") or 100)  # value size in bytes
HOT = int(os.environ.get("HOT") or 100)  # accounts shared by the contended workloads
RANGE = int(os.environ.get("RANGE") or 100)  # keys per range read
BATCH = 100  # keys per load transaction
CLUSTER_FILE = os.environ.get("FDB_CLUSTER_FILE", "/etc/foundationdb/fdb.cluster")
API_VERSION = 730
START_BALANCE = 1_000_000

ROW = b"bench/row/"  # ROW + %08d -> VALUE random bytes
ACCT = b"bench/acct/"  # ACCT + %04d -> little-endian int64 balance
CTR = b"bench/ctr/"  # CTR + %04d -> little-endian int64, only ever atomically added to
NOT_COMMITTED = 1020  # the transaction conflicted with a newer commit (serializability)


def rkey(i):
    return ROW + b"%08d" % i


def i64(v):
    return struct.pack("<q", v)


def from_i64(b):
    return struct.unpack("<q", bytes(b))[0]


# --- worker side (one process each, own FDB network thread) ---

def open_db():
    import fdb
    fdb.api_version(API_VERSION)
    return fdb, fdb.open(CLUSTER_FILE)


def run_tx(fdb, db, body, stats):
    """Retry loop like @fdb.transactional, counting retries per error code."""
    tr = db.create_transaction()
    while True:
        try:
            r = body(tr)
            tr.commit().wait()
            return r
        except fdb.FDBError as e:
            stats[e.code] = stats.get(e.code, 0) + 1
            tr.on_error(e).wait()  # backs off; re-raises if the error is not retryable


def worker(name, wid, barrier, out):
    fdb, db = open_db()
    rnd = random.Random(wid * 7919 + 1)
    lat, errors, extra = [], {}, {"keys": 0}
    val = os.urandom(VALUE)

    def point_read(tr):
        v = tr[rkey(rnd.randrange(ROWS))]
        assert v.present()
        extra["keys"] += 1

    def blind_write(tr):
        tr[rkey(rnd.randrange(ROWS))] = val
        return 1

    def transfer(tr):
        # read both balances, move 1 unit: a read-write conflict if another client
        # commits a write to either account after this transaction's read version
        a, b = rnd.sample(range(HOT), 2)
        ka, kb = ACCT + b"%04d" % a, ACCT + b"%04d" % b
        ba, bb = from_i64(tr[ka]), from_i64(tr[kb])
        tr[ka], tr[kb] = i64(ba - 1), i64(bb + 1)
        return 2

    def atomic_add(tr):
        # same hot set, but blind atomic ops add no read conflict ranges: never conflicts
        a, b = rnd.sample(range(HOT), 2)
        tr.add(CTR + b"%04d" % a, i64(1))
        tr.add(CTR + b"%04d" % b, i64(1))
        return 2

    def range_read(tr):
        s = rnd.randrange(max(1, ROWS - RANGE))
        n = len(list(tr.get_range(rkey(s), ROW + b"\xff", limit=RANGE)))
        extra["keys"] += n

    body = {"point reads": point_read, "blind writes": blind_write,
            "read-modify-write": transfer, "atomic adds": atomic_add, "range reads": range_read}.get(name)

    run_tx(fdb, db, lambda tr: tr.get_read_version().wait(), {})  # connect before timing
    barrier.wait()
    t_start = time.perf_counter()
    if name == "load":
        for s in range(wid * BATCH, ROWS, CLIENTS * BATCH):
            t0 = time.perf_counter()

            def load(tr, s=s):
                for i in range(s, min(s + BATCH, ROWS)):
                    tr[rkey(i)] = val
            run_tx(fdb, db, load, errors)
            lat.append(time.perf_counter() - t0)
            extra["keys"] += min(s + BATCH, ROWS) - s
    else:
        deadline = t_start + DURATION
        while (t0 := time.perf_counter()) < deadline:
            written = run_tx(fdb, db, body, errors)  # keys written; reads count theirs in body
            extra["keys"] += written or 0
            lat.append(time.perf_counter() - t0)
    out.put({"lat": lat, "errors": errors, "elapsed": time.perf_counter() - t_start, **extra})


def pct(sorted_lat, p):
    if not sorted_lat:
        return None
    return round(sorted_lat[min(len(sorted_lat) - 1, int(p / 100 * len(sorted_lat)))] * 1000, 2)


def run(name):
    ctx = mp.get_context("spawn")
    barrier, out = ctx.Barrier(CLIENTS), ctx.Queue()
    procs = [ctx.Process(target=worker, args=(name, w, barrier, out)) for w in range(CLIENTS)]
    for p in procs:
        p.start()
    parts = [out.get() for _ in procs]
    for p in procs:
        p.join()
    lat = sorted(x for r in parts for x in r["lat"])
    errors = {}
    for r in parts:
        for code, n in r["errors"].items():
            errors[code] = errors.get(code, 0) + n
    wall = max(r["elapsed"] for r in parts)
    txs = len(lat)
    conflicts = errors.get(NOT_COMMITTED, 0)
    res = {
        "transactions": txs,
        "seconds": round(wall, 2),
        "tx_per_s": round(txs / wall, 1),
        "keys_per_s": round(sum(r["keys"] for r in parts) / wall, 1),
        "p50_ms": pct(lat, 50), "p95_ms": pct(lat, 95), "p99_ms": pct(lat, 99),
        "max_ms": round(lat[-1] * 1000, 2) if lat else None,
        "conflicts": conflicts,
        # retries per committed transaction attempt: conflicts / (commits + conflicts)
        "conflict_rate": round(conflicts / (txs + conflicts), 4) if txs + conflicts else 0.0,
        "errors": {str(k): v for k, v in sorted(errors.items())},
    }
    print(f"==> {name}: {res['tx_per_s']:,.0f} tx/s  p50 {res['p50_ms']} ms  p99 {res['p99_ms']} ms"
          f"  conflicts {conflicts}", flush=True)
    return res


# --- parent side ---

def machine():
    mem = None
    try:
        with open("/proc/meminfo") as f:
            mem = round(int(f.readline().split()[1]) / 1024**2, 1)
    except OSError:
        pass
    return {"cpu_count": os.cpu_count(), "docker_vm_mem_gb": mem,  # as seen inside the Docker VM
            "arch": platform.machine(), "kernel": platform.release(),
            "python": platform.python_version()}


def main():
    fdb, db = open_db()
    status = json.loads(bytes(db[b"\xff\xff/status/json"]))
    cl = status["cluster"]
    procs = cl.get("processes", {}).values()
    server = sorted({p.get("version", "?") for p in procs})
    cfg = cl.get("configuration", {})
    info = {"server_version": ",".join(server), "processes": len(procs),
            "redundancy_mode": cfg.get("redundancy_mode"), "storage_engine": cfg.get("storage_engine"),
            "coordinators": len(status.get("client", {}).get("coordinators", {}).get("coordinators", []))}
    print(f"==> FoundationDB {info['server_version']} ({info['processes']} processes, "
          f"{info['redundancy_mode']}, {info['storage_engine']})  clients={CLIENTS} rows={ROWS} "
          f"value={VALUE}B duration={DURATION}s hot={HOT} range={RANGE}", flush=True)

    @fdb.transactional
    def reset(tr):
        del tr[b"bench/":b"bench0"]
        for a in range(HOT):
            tr[ACCT + b"%04d" % a] = i64(START_BALANCE)
    reset(db)

    results = {"load": run("load")}
    for name in ("point reads", "blind writes", "read-modify-write", "atomic adds", "range reads"):
        results[name] = run(name)

    # invariants: serializable transfers never create or lose money; atomic adds are never lost
    @fdb.transactional
    def totals(tr):
        bal = sum(from_i64(kv.value) for kv in tr.get_range_startswith(ACCT))
        ctr = sum(from_i64(kv.value) for kv in tr.get_range_startswith(CTR))
        return bal, ctr
    bal, ctr = totals(db)
    checks = {
        "sum_of_balances": bal, "expected_balances": HOT * START_BALANCE,
        "balances_ok": bal == HOT * START_BALANCE,
        "sum_of_counters": ctr, "expected_counters": 2 * results["atomic adds"]["transactions"],
        "counters_ok": ctr == 2 * results["atomic adds"]["transactions"],
    }

    res = {
        "db": "foundationdb", **info,
        "client": f"foundationdb (python) {pkg_version('foundationdb')}, api_version {API_VERSION}",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {"clients": CLIENTS, "rows": ROWS, "value_bytes": VALUE, "duration_s": DURATION,
                   "hot_accounts": HOT, "range_limit": RANGE, "load_batch": BATCH, "smoke": SMOKE},
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "results": results, "checks": checks,
    }

    lines = [f"FoundationDB {info['server_version']}  {info['processes']} processes, "
             f"{info['redundancy_mode']}, {info['storage_engine']}  |  {CLIENTS} clients, "
             f"{ROWS:,} rows x {VALUE} B, {DURATION:g} s/workload, hot set {HOT}  "
             f"({res['machine']['cpu_count']} CPU, {res['machine']['docker_vm_mem_gb']} GB)", "",
             f"  {'workload':<18} {'tx/s':>9} {'keys/s':>10} {'p50 ms':>8} {'p99 ms':>8} {'conflicts':>10}"]
    for name, r in results.items():
        conf = f"{r['conflicts']} ({r['conflict_rate']:.1%})"
        lines.append(f"  {name:<18} {r['tx_per_s']:>9,.0f} {r['keys_per_s']:>10,.0f} "
                     f"{r['p50_ms']:>8} {r['p99_ms']:>8} {conf:>10}")
    lines += ["", "  load: 1 tx = 100 keys; point read/blind write: 1 key per tx; read-modify-write: read 2 "
              "hot balances, write both;",
              "  atomic adds: 2 blind atomic adds on the same hot keys; range reads: get_range limit "
              f"{RANGE}. Latency includes retries.", "",
              f"  balances after {results['read-modify-write']['transactions']:,} transfers: "
              f"{bal:,} (expected {HOT * START_BALANCE:,}) -> {'OK' if checks['balances_ok'] else 'MISMATCH'}",
              f"  counters after {results['atomic adds']['transactions']:,} atomic tx: {ctr:,} "
              f"(expected {checks['expected_counters']:,}) -> {'OK' if checks['counters_ok'] else 'MISMATCH'}"]
    table = "\n".join(lines)
    print("\n" + table)

    os.makedirs("/results", exist_ok=True)
    stem = f"/results/foundationdb-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    with open(stem + ".json", "w") as f:
        json.dump(res, f, indent=2)
    with open(stem + ".txt", "w") as f:
        f.write(table + "\n")
    print(f"\n==> wrote results/{os.path.basename(stem)}.json and .txt")
    if not (checks["balances_ok"] and checks["counters_ok"]):
        raise SystemExit("invariant check failed")


if __name__ == "__main__":
    main()
