"""`make benchmark` for the NebulaGraph example: bulk load and query a seeded social graph.

The workload and harness are identical in the ArangoDB, Neo4j and NebulaGraph examples
(only the DB class at the bottom differs), so their results compare.
"""

from nebula3.Config import Config
from nebula3.gclient.net import ConnectionPool

import json
import multiprocessing as mp
import os
import platform
import random
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version

# --- workload (identical in the ArangoDB, Neo4j and NebulaGraph examples) ---
SMOKE = os.environ.get("SMOKE", "") not in ("", "0")
N = int(os.environ.get("N") or (10_000 if SMOKE else 100_000))  # persons
DEGREE = 10  # mean out-degree of `follows`
SEED = int(os.environ.get("SEED") or 42)
CLIENTS = int(os.environ.get("CLIENTS") or 8)
LOOKUPS = 1000 if SMOKE else 4000  # point lookups per run (split across the clients)
HOPS = 200 if SMOKE else 1000  # 1-hop / 2-hop start vertices
PATHS = 50 if SMOKE else 200  # shortest-path pairs
AGGS = 3 if SMOKE else 5  # top-10 aggregation repetitions
MAX_HOPS = 6  # shortest paths longer than this count as "not found"
CITIES = ["Berlin", "Paris", "Lisbon", "Madrid", "Rome", "Vienna", "Prague", "Warsaw",
          "Oslo", "Dublin", "Lahore", "Karachi", "Tokyo", "Seoul", "Austin", "Denver",
          "Lima", "Quito", "Cairo", "Accra"]


def generate():
    """Seeded social graph: persons with properties and a power-law-ish `follows` graph.

    Out-degree is Pareto-distributed (min 5, mean ~10, capped at 500); targets are drawn
    with a skewed popularity (rank ~ N * u**2.5), so a few persons have thousands of
    followers and most have a handful.
    """
    rng = random.Random(SEED)
    persons = [(i, f"user{i:07d}", f"Person {i}", rng.randint(18, 80), rng.choice(CITIES))
               for i in range(N)]
    popular = list(range(N))  # popularity rank -> person id
    rng.shuffle(popular)
    adj = []
    for s in range(N):
        k = min(int(rng.paretovariate(2.0) * DEGREE / 2), 50 * DEGREE)
        out = {popular[int(N * rng.random() ** 2.5)] for _ in range(k)}
        out.discard(s)
        adj.append(sorted(out))
    edges = [(s, d, rng.randint(2010, 2026)) for s in range(N) for d in adj[s]]
    return persons, adj, edges


# --- expected answers, computed in Python so every engine is checked the same way ---
def two_hop(adj, s):
    seen = set(adj[s])
    for x in adj[s]:
        seen.update(adj[x])
    seen.discard(s)
    return len(seen)


def bfs_hops(adj, a, b):
    if a == b:
        return 0
    seen, frontier = {a}, [a]
    for depth in range(1, MAX_HOPS + 1):
        nxt = []
        for v in frontier:
            for w in adj[v]:
                if w == b:
                    return depth
                if w not in seen:
                    seen.add(w)
                    nxt.append(w)
        frontier = nxt
    return None


# --- harness (identical in the three examples; only the DB class below differs) ---
def pct(lat):
    q = statistics.quantiles(lat, n=100, method="inclusive")
    return {"p50_ms": round(q[49], 2), "p95_ms": round(q[94], 2), "p99_ms": round(q[98], 2)}


def _worker(method, xs, barrier, queue, idx):
    c = Client()  # own process (no GIL contention) and own connection
    fn = getattr(c, method)
    for a in xs[:5]:  # warm-up, untimed (opens storage connections, fills caches)
        fn(*a)
    barrier.wait()
    lat, out, t0 = [], [], time.perf_counter()
    for a in xs:
        t = time.perf_counter()
        out.append(fn(*a))
        lat.append((time.perf_counter() - t) * 1000)
    t1 = time.perf_counter()  # CLOCK_MONOTONIC: comparable across processes
    c.close()
    queue.put((idx, t0, t1, lat, out))


def measure(method, args, expected, clients=1):
    """Call Client().<method>(*arg) for every arg, split over `clients` worker processes
    that start together; returns latency stats and how many answers differ from
    `expected` (computed in Python from the generated graph)."""
    ctx = mp.get_context("spawn")
    barrier, queue = ctx.Barrier(clients), ctx.Queue()
    procs = [ctx.Process(target=_worker, args=(method, args[i::clients], barrier, queue, i))
             for i in range(clients)]
    for p in procs:
        p.start()
    parts = sorted(queue.get() for _ in procs)
    for p in procs:
        p.join()
    wall = max(p[2] for p in parts) - min(p[1] for p in parts)
    got = [None] * len(args)
    for i, *_, out in parts:
        got[i::clients] = out
    lat = [x for p in parts for x in p[3]]
    wrong = sum(g != e for g, e in zip(got, expected))
    return {"clients": clients, "n": len(args), "qps": round(len(args) / wall, 1),
            **pct(lat), "wrong": wrong}, got


def machine():
    mem = None
    try:
        with open("/proc/meminfo") as f:
            mem = round(int(f.readline().split()[1]) / 1024**2, 1)
    except OSError:
        pass
    return {"cpu_count": os.cpu_count(),  # as seen inside the Docker VM
            "docker_vm_mem_gb": mem, "arch": platform.machine(),
            "kernel": platform.release(), "python": platform.python_version()}


def main(DB):
    t = time.perf_counter()
    persons, adj, edges = generate()
    rng = random.Random(SEED + 1)
    ids = [rng.randrange(N) for _ in range(max(LOOKUPS, HOPS))]
    pairs = []
    while len(pairs) < PATHS:
        a, b = rng.randrange(N), rng.randrange(N)
        if a != b:
            pairs.append((a, b))
    exp_paths = [bfs_hops(adj, a, b) for a, b in pairs]
    indeg = Counter(d for _, d, _ in edges)
    exp_top = sorted(indeg.values(), reverse=True)[:10]
    print(f"==> generated {N:,} persons, {len(edges):,} follows in "
          f"{time.perf_counter() - t:.1f}s (seed {SEED})", flush=True)

    db = DB()
    version = db.version()
    print(f"==> {db.name} {version} ({db.client_version})", flush=True)
    db.drop()  # leftovers from an interrupted run
    try:
        db.setup()
        load = {}
        for what, rows, fn in (("persons", persons, db.load_persons),
                               ("follows", edges, db.load_follows)):
            t0 = time.perf_counter()
            for i in range(0, len(rows), db.batch):
                fn(rows[i:i + db.batch])
            s = time.perf_counter() - t0
            load[what] = {"rows": len(rows), "batch": db.batch, "seconds": round(s, 2),
                          "rows_per_s": round(len(rows) / s, 1)}
            print(f"==> loaded {len(rows):,} {what} in {s:.1f}s", flush=True)
        counts = db.counts()
        if counts != (N, len(edges)):
            print(f"!! counts after load: {counts}, expected {(N, len(edges))}", flush=True)

        lk = [(persons[i][1],) for i in ids[:LOOKUPS]]
        lk_exp = [i for i in ids[:LOOKUPS]]
        hs = [(i,) for i in ids[:HOPS]]
        q = {}
        q["lookup_1"], _ = measure("lookup", lk, lk_exp)
        q[f"lookup_{CLIENTS}"], _ = measure("lookup", lk, lk_exp, CLIENTS)
        q["hop1"], _ = measure("hop1", hs, [len(adj[i]) for i in ids[:HOPS]])
        exp2 = [two_hop(adj, i) for i in ids[:HOPS]]
        q["hop2"], _ = measure("hop2", hs, exp2)
        q["hop2"]["avg_vertices"] = round(sum(exp2) / len(exp2), 1)
        q["path"], got = measure("path", pairs, exp_paths)
        q["path"]["found_pct"] = round(100 * sum(g is not None for g in got) / len(got), 1)
        q["path"]["expected_found_pct"] = round(
            100 * sum(e is not None for e in exp_paths) / len(exp_paths), 1)
        q["top10"], _ = measure("top10", [()] * AGGS, [exp_top] * AGGS)
    finally:
        db.drop()
        db.close()

    res = {
        "db": db.name, "db_version": version, "client": db.client_version,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {"N": N, "edges": len(edges), "mean_out_degree": round(len(edges) / N, 2),
                   "max_in_degree": exp_top[0], "seed": SEED, "clients": CLIENTS,
                   "max_path_hops": MAX_HOPS, "db_settings": db.settings},
        "machine": machine(),
        "limits": json.loads(os.environ.get("BENCH_LIMITS") or "null"),  # bench/limits.sh
        "results": {"load": load, "counts_after_load": counts, "queries": q},
    }
    m = res["machine"]
    print(f"\n{db.name} {version}  N={N:,} persons, {len(edges):,} follows "
          f"(max in-degree {exp_top[0]:,})  ({m['cpu_count']} CPU, {m['docker_vm_mem_gb']} GB)")
    for what, x in load.items():
        print(f"  load {what:<8} batch {x['batch']:<6} {x['rows_per_s']:>12,.0f} rows/s"
              f"   {x['seconds']:.1f} s")
    print(f"  {'query':<22}{'n':>6}{'QPS':>9}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}  check")
    labels = {"lookup_1": "lookup by handle, 1c", f"lookup_{CLIENTS}":
              f"lookup by handle, {CLIENTS}c", "hop1": "1-hop count", "hop2": "2-hop count",
              "path": f"shortest path <={MAX_HOPS}", "top10": "top-10 most-followed"}
    for k, x in q.items():
        note = "ok" if not x["wrong"] else f"{x['wrong']} wrong"
        if k == "hop2":
            note += f" (avg {x['avg_vertices']:.0f} vertices)"
        if k == "path":
            note += f" (found {x['found_pct']:.0f}%)"
        print(f"  {labels[k]:<22}{x['n']:>6}{x['qps']:>9,.0f}{x['p50_ms']:>9.2f}"
              f"{x['p95_ms']:>9.2f}{x['p99_ms']:>9.2f}  {note}")

    os.makedirs("/results", exist_ok=True)
    path = f"/results/{db.name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    with open(path, "w") as f:
        json.dump(res, f, indent=2)
    if os.environ.get("HOST_UID"):  # keep results/ removable by the host user on Linux
        for p in ("/results", path):
            os.chown(p, int(os.environ["HOST_UID"]), int(os.environ.get("HOST_GID") or 0))
    print(f"\n==> wrote results/{os.path.basename(path)}")


# --- NebulaGraph: space `bench` (INT64 vids = person ids), dropped at the end ---
GRAPHD, USER, PASSWORD = ("graphd", 9669), "root", "nebula"
HEARTBEAT_S = 2  # --heartbeat_interval_secs in docker-compose.yml


def pool(size=1):
    cfg = Config()
    cfg.max_connection_pool_size = size
    p = ConnectionPool()
    if not p.init([GRAPHD], cfg):
        raise RuntimeError("cannot connect to graphd")
    return p


def execute(session, stmt):
    r = session.execute(stmt)
    if not r.is_succeeded():
        raise RuntimeError(f"{r.error_msg()}: {stmt[:200]}")
    return r


class Client:
    def __init__(self):
        self.pool = pool()  # one graphd connection/session per worker
        self.s = self.pool.get_session(USER, PASSWORD)
        execute(self.s, "USE bench")

    def one(self, stmt):
        r = execute(self.s, stmt)
        return r.row_values(0)[0].as_int() if r.row_size() else None

    def lookup(self, handle):  # tag index person_handle
        return self.one(f'LOOKUP ON person WHERE person.handle == "{handle}" YIELD id(vertex) '
                        "AS id, properties(vertex).name AS name, properties(vertex).age AS age")

    def hop1(self, s):
        return self.one(f"GO FROM {s} OVER follows YIELD dst(edge) AS d | YIELD count(*) AS n")

    def hop2(self, s):
        return self.one(f"GO 1 TO 2 STEPS FROM {s} OVER follows WHERE dst(edge) != {s} "
                        "YIELD DISTINCT dst(edge) AS d | YIELD count(*) AS n")

    def path(self, a, b):
        return self.one(f"FIND SHORTEST PATH FROM {a} TO {b} OVER follows UPTO {MAX_HOPS} "
                        "STEPS YIELD path AS p | YIELD length($-.p) AS n | LIMIT 1")

    def top10(self):  # full scan through the edge index, grouped in graphd
        r = execute(self.s, "LOOKUP ON follows YIELD dst(edge) AS d "
                            "| GROUP BY $-.d YIELD count(*) AS n | ORDER BY $-.n DESC | LIMIT 10")
        return [v.as_int() for v in r.column_values("n")]

    def close(self):
        self.s.release()
        self.pool.close()


class DB:
    name = "nebula-graph"
    batch = 2_000
    client_version = f"nebula3-python {pkg_version('nebula3-python')}"
    settings = {"load": "batched INSERT VERTEX / INSERT EDGE (2000 rows per statement), "
                        "one client",
                "space": "partition_num 10, replica_factor 1, vid_type INT64",
                "indexes": "tag index person(handle), edge index follows() for the top-10 scan"}

    def __init__(self):
        self.pool = pool(2)
        self.s = self.pool.get_session(USER, PASSWORD)

    def ex(self, stmt):
        return execute(self.s, stmt)

    def retry(self, stmt, secs=60):  # schema reaches graphd/storaged via metad heartbeats
        for _ in range(secs):
            if self.s.execute(stmt).is_succeeded():
                return
            time.sleep(1)
        self.ex(stmt)

    def version(self):
        return self.ex("SHOW HOSTS").column_values("Version")[0].as_string()

    def setup(self):
        self.ex("CREATE SPACE bench (partition_num = 10, replica_factor = 1, vid_type = INT64)")
        self.retry("USE bench")
        self.ex("CREATE TAG person (handle string, name string, age int, city string)")
        self.ex("CREATE EDGE follows (since int)")
        self.ex("CREATE TAG INDEX person_handle ON person (handle(16))")
        self.ex("CREATE EDGE INDEX follows_all ON follows ()")
        # a probe write succeeds once storaged has the schema; indexes need a few more beats
        self.retry('INSERT VERTEX person (handle, name, age, city) VALUES -1:("", "", 0, "")')
        self.retry("INSERT EDGE follows (since) VALUES -1->-1:(0)")
        self.ex("DELETE VERTEX -1 WITH EDGE")
        time.sleep(3 * HEARTBEAT_S + 2)

    def load_persons(self, rows):
        self.ex("INSERT VERTEX person (handle, name, age, city) VALUES " + ", ".join(
            f'{i}:("{h}", "{n}", {a}, "{c}")' for i, h, n, a, c in rows))

    def load_follows(self, rows):
        self.ex("INSERT EDGE follows (since) VALUES " + ", ".join(
            f"{s}->{d}:({y})" for s, d, y in rows))

    def counts(self):  # through the indexes, so this also checks they cover every row
        n = self.ex("LOOKUP ON person YIELD id(vertex) AS v | YIELD count(*) AS n")
        e = self.ex("LOOKUP ON follows YIELD src(edge) AS s | YIELD count(*) AS n")
        return n.row_values(0)[0].as_int(), e.row_values(0)[0].as_int()

    def drop(self):
        self.ex("DROP SPACE IF EXISTS bench")

    def close(self):
        self.s.release()
        self.pool.close()


if __name__ == "__main__":
    main(DB)
