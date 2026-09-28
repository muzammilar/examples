# Qdrant — single node

One Qdrant node with default config, REST on `localhost:6333`, gRPC on `localhost:6334`.
`make test` drives the REST API with curl + jq from a small `tools` container.

```bash
make up       # start and wait for /readyz
make test     # run scripts/test.sh: collection, payload index, upsert, k-NN, filtered k-NN, recommend
make status   # version and collections
make cli      # shell with curl + jq on the compose network (API at http://qdrant:6333)
make benchmark [SMOKE=1]  # insert / index / search / recall benchmark (bench/bench.py, needs `make up`)
make down     # remove containers, volumes and the locally built tools image
```

- REST: http://localhost:6333 (e.g. `curl localhost:6333/collections`)
- Web UI: http://localhost:6333/dashboard
- gRPC: `localhost:6334`

Ports are bound to localhost because there is no auth (no API key is set). Anonymous
telemetry is off (`QDRANT__TELEMETRY_DISABLED`); the local `/telemetry` endpoint that
`make status` reads still works. Request bodies are in `requests/`; `make test` drops and
recreates the `demo` collection each run. `make status` uses curl and jq on the host.

## Benchmark

`make benchmark` runs [`bench/bench.py`](bench/bench.py) in the official uv image
(`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`, deps pinned in `bench/uv.lock`, no
host Python needed). The uv cache and venv sit in the `uv-cache` volume, so reruns start in
seconds; it belongs to the `bench` profile, so `make down` keeps it
(`docker volume rm qdrant-single_uv-cache` drops it). It talks gRPC to `qdrant:6334`. The
workload is the same as in
[`../../weaviate/single-node`](../../weaviate/single-node) and
[`../../milvus/single-node`](../../milvus/single-node), so results compare:

1. **Insert**: N seeded random unit vectors (float32, dim 128, cosine) with an integer
   payload `tag` in [0, 10), upserted in batches of 1000 (`wait=true`), one client.
2. **Time to queryable**: HNSW building is deferred during the load (`indexing_threshold: 0`),
   then enabled; timed until the collection is green with every vector indexed.
   `full_scan_threshold` is lowered to 10 KB so searches use the graph: with the 10 MB default,
   Qdrant brute-forces every segment under ~20k vectors, which at N=100k is all of them.
3. **Search**: 1000 k=10 queries, p50/p95/p99 latency and QPS with 1 client, then 8
   concurrent clients (one connection each).
4. **Recall@10** of the first 200 queries against exact numpy brute force.
5. **Filtered search**: `tag == 0` (~10%, payload-indexed), latency/QPS and recall@10 against
   brute force over the matching subset (filterable HNSW with Qdrant's extra payload links).

HNSW is set explicitly and identically in all three examples: `M=16`, `ef_construction=128`,
search `ef=64`. Defaults: `N=100000`; `SMOKE=1` uses `N=10000`; override with `N`, `DIM`, `K`
(e.g. `make benchmark N=1000000`). Results print as a table and are written to
`results/qdrant-<timestamp>.json` (git-ignored) with the Qdrant version, parameters, and the
CPU count and memory of the Docker VM.

### Sample results

TODO: fill from a run on a quiet machine (`make benchmark`, N=100000, dim 128).

| metric | value |
| --- | --- |
| insert (vec/s) | TODO |
| time to queryable (s) | TODO |
| search, 1 client: QPS / p50 / p99 (ms) | TODO |
| search, 8 clients: QPS / p50 / p99 (ms) | TODO |
| filtered (~10%), 1 client: QPS / p50 / p99 (ms) | TODO |
| recall@10 / filtered recall@10 | TODO |
