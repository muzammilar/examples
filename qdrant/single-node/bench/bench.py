"""Qdrant benchmark: batch insert, time to queryable, k-NN latency/QPS (1 and 8 clients),
recall@k vs numpy brute force, and filtered search. Run with `make benchmark [SMOKE=1]`."""
import json
import os
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from importlib.metadata import version as pkg_version

import numpy as np

# --- workload (identical across the Qdrant, Weaviate and Milvus examples) ---
SMOKE = os.environ.get("SMOKE", "") not in ("", "0")
N = int(os.environ.get("N") or (10_000 if SMOKE else 100_000))
DIM = int(os.environ.get("DIM") or 128)
K = int(os.environ.get("K") or 10)
BATCH = int(os.environ.get("BATCH") or 1000)
QUERIES = int(os.environ.get("QUERIES") or 1000)
RECALL_QUERIES = int(os.environ.get("RECALL_QUERIES") or 200)
CLIENTS = int(os.environ.get("CLIENTS") or 8)
SEED = int(os.environ.get("SEED") or 42)
TAGS = 10  # payload `tag` is uniform in [0, 10); the filter `tag == 0` selects ~10%
# HNSW set explicitly to the same values everywhere (M = max links per node on upper
# layers, 2*M on layer 0 in all three engines); cosine distance on unit vectors.
HNSW = {"M": 16, "ef_construction": 128, "ef_search": 64}
C = "bench"


# --- Qdrant (gRPC via qdrant-client) ---
from qdrant_client import QdrantClient, models  # noqa: E402

HOST = os.environ.get("QDRANT_HOST", "qdrant")
FILTER = models.Filter(must=[models.FieldCondition(key="tag", match=models.MatchValue(value=0))])
PARAMS = models.SearchParams(hnsw_ef=HNSW["ef_search"])


def connect():
    return QdrantClient(host=HOST, grpc_port=6334, prefer_grpc=True, timeout=300)


class Searcher:
    def __init__(self):
        self.c = connect()

    def __call__(self, q, filt):
        pts = self.c.query_points(C, query=q.tolist(), limit=K, query_filter=FILTER if filt else None,
                                  search_params=PARAMS, with_payload=False).points
        return [p.id for p in pts]

    def close(self):
        self.c.close()


class DB:
    name = "qdrant"
    client_version = f"qdrant-client {pkg_version('qdrant-client')}"
    # HNSW is deferred during the load (indexing_threshold=0), then built in one go;
    # indexing_threshold=1 KB afterwards so every segment gets an HNSW graph, and
    # full_scan_threshold=10 KB so searches use it (the 10 MB default would brute-force
    # every segment below ~20k vectors, i.e. all of them at N=100k).
    settings = {"transport": "grpc", "payload_index": "tag (integer)",
                "indexing_threshold_kb": "0 during load, then 1", "full_scan_threshold_kb": 10,
                "other": "defaults"}

    def __init__(self):
        self.c = connect()

    def version(self):
        return self.c.info().version

    def setup(self):
        if self.c.collection_exists(C):
            self.c.delete_collection(C)
        self.c.create_collection(
            C,
            vectors_config=models.VectorParams(size=DIM, distance=models.Distance.COSINE),
            hnsw_config=models.HnswConfigDiff(m=HNSW["M"], ef_construct=HNSW["ef_construction"],
                                               full_scan_threshold=10),
            optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0),
        )
        self.c.create_payload_index(C, "tag", models.PayloadSchemaType.INTEGER, wait=True)

    def insert(self, ids, vecs, tags):
        self.c.upsert(C, wait=True, points=models.Batch(
            ids=ids.tolist(), vectors=vecs.tolist(), payloads=[{"tag": int(t)} for t in tags]))

    def build(self):
        """Enable indexing, then wait until the collection is green with every vector indexed."""
        t0 = time.perf_counter()
        self.c.update_collection(C, optimizers_config=models.OptimizersConfigDiff(indexing_threshold=1))
        while True:
            info = self.c.get_collection(C)
            if info.status == models.CollectionStatus.GREEN and (info.indexed_vectors_count or 0) >= N:
                break
            if time.perf_counter() - t0 > 3600:
                raise TimeoutError(f"not indexed after 1h: {info.status} {info.indexed_vectors_count}/{N}")
            time.sleep(0.2)
        return {"hnsw_build": round(time.perf_counter() - t0, 2)}

    def searcher(self):
        return Searcher()

    def close(self):
        self.c.close()


# --- harness (identical across the three examples; only the DB class above differs) ---
def unit(a):
    return (a / np.linalg.norm(a, axis=1, keepdims=True)).astype(np.float32)


def exact_topk(data, qs, mask=None):
    """Brute-force top-K ids by cosine (dot product of unit vectors)."""
    sims = qs @ data.T
    if mask is not None:
        sims[:, ~mask] = -np.inf
    part = np.argpartition(-sims, K, axis=1)[:, :K]
    order = np.take_along_axis(sims, part, axis=1).argsort(axis=1)[:, ::-1]
    return np.take_along_axis(part, order, axis=1)


def recall(got, truth):
    return float(np.mean([len(set(g) & set(t.tolist())) / K for g, t in zip(got, truth)]))


def run_queries(db, qs, filt, clients):
    """Split qs across `clients` workers, each with its own connection; time every call."""
    searchers = [db.searcher() for _ in range(clients)]
    for s in searchers:  # warm-up, untimed
        for q in qs[:10]:
            s(q, filt)
    chunks = np.array_split(np.arange(len(qs)), clients)

    def worker(arg):
        search, idx = arg
        lat, out = [], {}
        for i in idx:
            t = time.perf_counter()
            out[i] = search(qs[i], filt)
            lat.append(time.perf_counter() - t)
        return lat, out

    t0 = time.perf_counter()
    with ThreadPoolExecutor(clients) as ex:
        parts = list(ex.map(worker, zip(searchers, chunks)))
    wall = time.perf_counter() - t0
    lat = np.array([x for p in parts for x in p[0]]) * 1000
    ids = {i: v for p in parts for i, v in p[1].items()}
    for s in searchers:
        getattr(s, "close", lambda: None)()
    stats = {
        "clients": clients,
        "queries": len(qs),
        "qps": round(len(qs) / wall, 1),
        "p50_ms": round(float(np.percentile(lat, 50)), 2),
        "p95_ms": round(float(np.percentile(lat, 95)), 2),
        "p99_ms": round(float(np.percentile(lat, 99)), 2),
    }
    return stats, [ids[i] for i in range(len(qs))]


def machine():
    mem = None
    try:
        with open("/proc/meminfo") as f:
            mem = round(int(f.readline().split()[1]) / 1024**2, 1)
    except OSError:
        pass
    return {
        "cpu_count": os.cpu_count(),  # as seen inside the Docker VM
        "docker_vm_mem_gb": mem,
        "arch": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
    }


def main():
    rng = np.random.default_rng(SEED)
    data = unit(rng.standard_normal((N, DIM), dtype=np.float32))
    tags = rng.integers(0, TAGS, N)
    qs = unit(rng.standard_normal((QUERIES, DIM), dtype=np.float32))
    ids = np.arange(N)

    db = DB()
    print(f"==> {db.name} {db.version()}  N={N} dim={DIM} k={K} batch={BATCH} "
          f"queries={QUERIES} clients={CLIENTS} HNSW={HNSW}", flush=True)
    db.setup()

    t0 = time.perf_counter()
    for s in range(0, N, BATCH):
        db.insert(ids[s:s + BATCH], data[s:s + BATCH], tags[s:s + BATCH])
    insert_s = time.perf_counter() - t0
    print(f"==> inserted {N} in {insert_s:.1f}s", flush=True)

    t0 = time.perf_counter()
    build = db.build()
    ready_s = time.perf_counter() - t0
    print(f"==> queryable after {ready_s:.1f}s {build}", flush=True)

    single, got = run_queries(db, qs, False, 1)
    multi, _ = run_queries(db, qs, False, CLIENTS)
    filt, got_f = run_queries(db, qs, True, 1)
    rq = qs[:RECALL_QUERIES]
    r = recall(got[:RECALL_QUERIES], exact_topk(data, rq))
    rf = recall(got_f[:RECALL_QUERIES], exact_topk(data, rq, tags == 0))

    res = {
        "db": db.name,
        "db_version": db.version(),
        "client": db.client_version,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "params": {"N": N, "dim": DIM, "k": K, "batch": BATCH, "queries": QUERIES,
                   "recall_queries": RECALL_QUERIES, "clients": CLIENTS, "seed": SEED,
                   "metric": "cosine", "filter": f"tag == 0 (~{100 // TAGS}%)",
                   "hnsw": HNSW, "db_settings": db.settings},
        "machine": machine(),
        "results": {
            "insert_s": round(insert_s, 2),
            "insert_vps": round(N / insert_s, 1),
            "time_to_queryable_s": round(ready_s, 2),
            "build_phases_s": build,
            "search": single,
            "search_concurrent": multi,
            "filtered_search": filt,
            f"recall@{K}": round(r, 4),
            f"filtered_recall@{K}": round(rf, 4),
        },
    }
    x = res["results"]
    rows = [
        ("insert (batch %d)" % BATCH, f"{x['insert_vps']:,.0f} vec/s", f"{insert_s:.1f} s total"),
        ("time to queryable", f"{ready_s:.1f} s", " ".join(f"{k}={v}" for k, v in build.items())),
    ]
    for label, st in (("search, 1 client", single), (f"search, {CLIENTS} clients", multi),
                      ("filtered, 1 client", filt)):
        rows.append((label, f"{st['qps']:,.0f} QPS",
                     f"p50 {st['p50_ms']} / p95 {st['p95_ms']} / p99 {st['p99_ms']} ms"))
    rows += [(f"recall@{K}", f"{r:.4f}", f"{RECALL_QUERIES} queries vs numpy brute force"),
             (f"filtered recall@{K}", f"{rf:.4f}", "tag == 0")]
    print(f"\n{db.name} {res['db_version']}  N={N:,} dim={DIM} k={K}  "
          f"M={HNSW['M']} efC={HNSW['ef_construction']} ef={HNSW['ef_search']}  "
          f"({res['machine']['cpu_count']} CPU, {res['machine']['docker_vm_mem_gb']} GB)")
    print("\n".join(f"  {a:<22} {b:>14}   {c}" for a, b, c in rows))

    os.makedirs("/results", exist_ok=True)
    path = f"/results/{db.name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    with open(path, "w") as f:
        json.dump(res, f, indent=2)
    print(f"\n==> wrote results/{os.path.basename(path)}")
    db.close()


if __name__ == "__main__":
    main()
