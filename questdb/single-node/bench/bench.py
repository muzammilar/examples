"""`make benchmark` (bench service, uv image): ILP ingest rate, then query timings.

1. Drops and recreates bench_trades and bench_quotes (WAL, PARTITION BY DAY), the same shape
   as the sql/ walkthrough, with SYMBOLS symbols.
2. Ingest: PROCS processes, each with its own ILP-over-HTTP Sender (official `questdb` Python
   client, which wraps the C client), send ROWS rows in total (1/3 trades, 2/3 quotes) as
   pandas DataFrames of CHUNK rows (one HTTP request each). Every process owns a slice of the
   symbols and walks the same 3 days, so the processes' commits overlap in time and the WAL
   apply has to merge them (out-of-order). Reports two rates: `sent` (all requests acked: the
   rows are durable in the WAL) and `visible` (the WAL is applied and count() sees every row).
3. Queries over HTTP /exec, each REPEAT times after one warm-up run: wall-clock latency at the
   client (min / p50 / max) and the server's own `execute` timing.

Prints a table and writes /results/$NAME.json (parameters, limits, Docker VM, results), then
drops both tables to give the disk space back (KEEP=1 keeps them).
"""

import json
import multiprocessing as mp
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd
from questdb import Sender

HOST = os.environ.get("QDB_HOST", "questdb")
SMOKE = os.environ.get("SMOKE") == "1"
ROWS = int(os.environ.get("ROWS") or (3_000_000 if SMOKE else 20_000_000))
PROCS = int(os.environ.get("PROCS") or 8)
CHUNK = int(os.environ.get("CHUNK") or 100_000)
SYMBOLS = int(os.environ.get("SYMBOLS") or 100)
REPEAT = int(os.environ.get("REPEAT") or 5)
NAME = os.environ.get("NAME") or "questdb-single"
KEEP = os.environ.get("KEEP") == "1"

T0 = pd.Timestamp("2026-09-29T00:00:00Z").value // 1000  # micros
SPAN = 3 * 86_400 * 1_000_000  # 3 days in micros
DAY2 = "2026-09-30"


def sql(query, timings=False):
    url = f"http://{HOST}:9000/exec?" + urllib.parse.urlencode(
        {"query": query, "timings": str(timings).lower()})
    try:
        with urllib.request.urlopen(url, timeout=600) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:  # QuestDB answers SQL errors with 400 + a JSON body
        body = json.load(e)
    if "error" in body:
        raise RuntimeError(f"{query}: {body['error']}")
    return body


def scalar(query):
    return sql(query)["dataset"][0][0]


def setup():
    for t in ("bench_trades", "bench_quotes"):
        sql(f"DROP TABLE IF EXISTS {t}")
    sql("""CREATE TABLE bench_trades (symbol SYMBOL CAPACITY 1024, side SYMBOL, price DOUBLE,
           size DOUBLE, ts TIMESTAMP) TIMESTAMP(ts) PARTITION BY DAY WAL""")
    sql("""CREATE TABLE bench_quotes (symbol SYMBOL CAPACITY 1024, bid DOUBLE, ask DOUBLE,
           bid_size DOUBLE, ask_size DOUBLE, ts TIMESTAMP) TIMESTAMP(ts) PARTITION BY DAY WAL""")


def template(rng, syms, rows, kind):
    """One CHUNK-row DataFrame; later chunks only get a shifted ts column."""
    sym = rng.choice(syms, rows)
    base = np.array([100.0 + 10 * int(s[3:]) for s in sym])
    mid = base * (1 + rng.normal(0, 0.001, rows))
    if kind == "trades":
        df = pd.DataFrame({
            "symbol": pd.Categorical(sym),
            "side": pd.Categorical(rng.choice(["buy", "sell"], rows)),
            "price": mid.round(2),
            "size": rng.integers(1, 1000, rows).astype(np.float64),
        })
    else:
        df = pd.DataFrame({
            "symbol": pd.Categorical(sym),
            "bid": (mid * 0.99995).round(2),
            "ask": (mid * 1.00005).round(2),
            "bid_size": rng.integers(1, 5000, rows).astype(np.float64),
            "ask_size": rng.integers(1, 5000, rows).astype(np.float64),
        })
    return df


def worker(i, start, done):
    rng = np.random.default_rng(i)
    syms = [f"SYM{s:03d}" for s in range(i, SYMBOLS, PROCS)]
    plan = []  # (table, template, rows in this process, micros between rows)
    for table, kind, total in (("bench_trades", "trades", ROWS // 3),
                               ("bench_quotes", "quotes", ROWS - ROWS // 3)):
        n = total // PROCS + (1 if i < total % PROCS else 0)
        plan.append((table, template(rng, syms, min(CHUNK, n), kind), n, SPAN / max(n, 1)))
    conf = f"http::addr={HOST}:9000;auto_flush=off;request_timeout=600000;"
    with Sender.from_conf(conf) as sender:
        start.wait()
        # interleave the two tables chunk by chunk, both walking forward through the 3 days
        offsets = [0, 0]
        while any(offsets[k] < plan[k][2] for k in range(2)):
            for k, (table, df, n, step) in enumerate(plan):
                if offsets[k] >= n:
                    continue
                rows = min(len(df), n - offsets[k])
                chunk = df.iloc[:rows].copy()
                micros = T0 + ((np.arange(rows) + offsets[k]) * step).astype(np.int64) + i
                chunk["ts"] = pd.to_datetime(micros, unit="us", utc=True)
                sender.dataframe(chunk, table_name=table, symbols=["symbol", "side"]
                                 if table == "bench_trades" else ["symbol"], at="ts")
                sender.flush()
                offsets[k] += rows
    done.put((i, time.perf_counter()))


def wal_pending():
    rows = sql("SELECT name, writerTxn, sequencerTxn, suspended FROM wal_tables() "
               "WHERE name IN ('bench_trades', 'bench_quotes')")["dataset"]
    for name, w, s, suspended in rows:
        if suspended:
            raise RuntimeError(f"WAL apply suspended for {name}")
    return sum(s - w for _, w, s, _ in rows)


def ingest():
    start, done = mp.Barrier(PROCS + 1), mp.Queue()
    procs = [mp.Process(target=worker, args=(i, start, done)) for i in range(PROCS)]
    for p in procs:
        p.start()
    start.wait()
    t0 = time.perf_counter()
    ends = [done.get()[1] for _ in procs]
    for p in procs:
        p.join()
        if p.exitcode:
            raise SystemExit(f"ingest worker failed (exit {p.exitcode})")
    t_sent = max(ends) - t0
    while wal_pending():
        time.sleep(0.05)
    count = scalar("SELECT count() FROM bench_trades") + scalar("SELECT count() FROM bench_quotes")
    t_visible = time.perf_counter() - t0
    if count != ROWS:
        raise SystemExit(f"expected {ROWS} rows, found {count}")
    return {"rows": ROWS, "procs": PROCS, "chunk": CHUNK, "sent_s": round(t_sent, 3),
            "visible_s": round(t_visible, 3), "sent_rows_per_s": round(ROWS / t_sent),
            "visible_rows_per_s": round(ROWS / t_visible)}


QUERIES = [
    ("count", "SELECT count() FROM bench_trades"),
    ("interval 1h", f"SELECT count(), avg(price) FROM bench_trades WHERE ts IN '{DAY2}T12;1h'"),
    ("ohlc 1 symbol 1m",
     "SELECT ts, first(price), max(price), min(price), last(price), sum(size) "
     "FROM bench_trades WHERE symbol = 'SYM007' SAMPLE BY 1m"),
    ("vwap all 1h",
     "SELECT ts, symbol, sum(price * size) / sum(size) FROM bench_trades SAMPLE BY 1h"),
    ("latest on", "SELECT * FROM bench_quotes LATEST ON ts PARTITION BY symbol"),
    ("asof join 1h",
     "SELECT count(), avg(t.price - (q.bid + q.ask) / 2) FROM bench_trades t "
     f"ASOF JOIN bench_quotes q ON (symbol) WHERE t.ts IN '{DAY2}T12;1h'"),
    ("asof join 1d",
     "SELECT t.symbol, count(), avg(t.price - (q.bid + q.ask) / 2) FROM bench_trades t "
     f"ASOF JOIN bench_quotes q ON (symbol) WHERE t.ts IN '{DAY2}' GROUP BY t.symbol"),
]


def queries():
    out = []
    for name, q in QUERIES:
        body = sql(q)  # warm-up (compiles, pages in the columns)
        result_rows = body["count"]
        wall, server = [], []
        for _ in range(REPEAT):
            t = time.perf_counter()
            body = sql(q, timings=True)
            wall.append((time.perf_counter() - t) * 1000)
            server.append(body["timings"]["execute"] / 1e6)
        out.append({"query": name, "sql": q, "result_rows": result_rows,
                    "wall_ms_min": round(min(wall), 2), "wall_ms_p50": round(statistics.median(wall), 2),
                    "wall_ms_max": round(max(wall), 2),
                    "server_execute_ms_p50": round(statistics.median(server), 2)})
    return out


def main():
    version = scalar("SELECT build()")
    print(f"==> {version}")
    print(f"==> ingest: {ROWS:,} rows ({ROWS // 3:,} trades + {ROWS - ROWS // 3:,} quotes, "
          f"{SYMBOLS} symbols, 3 days) from {PROCS} ILP/HTTP senders, {CHUNK:,}-row requests")
    setup()
    ing = ingest()
    print(f"    sent (WAL, acked):   {ing['sent_s']:>7.2f} s  {ing['sent_rows_per_s']:>12,} rows/s")
    print(f"    visible (applied):   {ing['visible_s']:>7.2f} s  {ing['visible_rows_per_s']:>12,} rows/s")
    print(f"==> queries ({REPEAT} runs each after a warm-up, ms)")
    qs = queries()
    print(f"    {'query':<18} {'rows':>6} {'min':>8} {'p50':>8} {'max':>8} {'server p50':>11}")
    for r in qs:
        print(f"    {r['query']:<18} {r['result_rows']:>6} {r['wall_ms_min']:>8} "
              f"{r['wall_ms_p50']:>8} {r['wall_ms_max']:>8} {r['server_execute_ms_p50']:>11}")
    size = sql("SELECT sum(diskSize) FROM (SELECT diskSize FROM table_partitions('bench_trades') "
               "UNION ALL SELECT diskSize FROM table_partitions('bench_quotes'))")["dataset"][0][0]
    print(f"==> {size / 2**20:,.0f} MiB on disk for both tables")
    res = {"name": NAME, "version": version, "host": os.environ.get("HOST_INFO", ""),
           "docker": os.environ.get("DOCKER_INFO", ""),
           "limits": json.loads(os.environ.get("BENCH_LIMITS") or "{}"),
           "params": {"rows": ROWS, "procs": PROCS, "chunk": CHUNK, "symbols": SYMBOLS,
                      "repeat": REPEAT},
           "ingest": ing, "queries": qs, "disk_bytes": size}
    with open(f"/results/{NAME}.json", "w") as f:
        json.dump(res, f, indent=2)
    print(f"==> results/{NAME}.json")
    if not KEEP:
        for t in ("bench_trades", "bench_quotes"):
            sql(f"DROP TABLE {t}")
        print("==> dropped bench_trades and bench_quotes (KEEP=1 keeps them)")


if __name__ == "__main__":
    main()
