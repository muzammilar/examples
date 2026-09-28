# Weaviate — single node

One Weaviate node with no vectorizer module (`vectorizer: none`): the client supplies
every vector. `make test` drives REST and GraphQL with curl + jq from a small `tools` container.

```bash
make up       # start and wait for /v1/.well-known/ready
make test     # run scripts/test.sh: collection, batch insert, index wait, nearVector, filtered nearVector, BM25, hybrid
make status   # node status, collections, and the Landmark object count (GraphQL Aggregate)
make cli      # shell with curl + jq on the compose network (API at http://weaviate:8080)
make benchmark [SMOKE=1]  # insert / index / search / recall benchmark (bench/bench.py, needs `make up`)
make down     # remove containers, volumes and the locally built tools image
```

- REST: http://localhost:8080/v1 (e.g. `curl localhost:8080/v1/schema`)
- GraphQL: `POST http://localhost:8080/v1/graphql`
- gRPC: `localhost:50051`

No web UI ships with the server. Anonymous access is enabled, no API key; ports are bound to
localhost because there is no auth. Usage telemetry is off (`DISABLE_TELEMETRY`), and
`ASYNC_INDEXING` is on: vectors are indexed from a background queue and are not returned by
vector search until indexed, so `make test` waits for the queue to drain after its insert
(`/v1/nodes/Landmark?output=verbose`). Request bodies are in `requests/`; `make test` drops and recreates the
`Landmark` collection each run. `make status` uses curl and jq on the host.

## Benchmark

`make benchmark` runs [`bench/bench.py`](bench/bench.py) in the official uv image
(`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`, deps pinned in `bench/uv.lock`, no
host Python needed). The uv cache and venv sit in the `uv-cache` volume, so reruns start in
seconds; it belongs to the `bench` profile, so `make down` keeps it
(`docker volume rm weaviate-single_uv-cache` drops it). It uses the v4 client (gRPC on
`weaviate:50051`). The workload is the same as in [`../../qdrant/single-node`](../../qdrant/single-node)
and [`../../milvus/single-node`](../../milvus/single-node), so results compare:

1. **Insert**: N seeded random unit vectors (float32, dim 128, cosine) with an int property
   `tag` in [0, 10), `insert_many` in batches of 1000, one client (collection `Bench`).
2. **Time to queryable**: with async indexing, timed until every shard's vector queue is
   empty and its indexing status is `READY` (`/v1/nodes?output=verbose`).
3. **Search**: 1000 k=10 `nearVector` queries, p50/p95/p99 latency and QPS with 1 client,
   then 8 concurrent clients (one connection each).
4. **Recall@10** of the first 200 queries against exact numpy brute force.
5. **Filtered search**: `tag == 0` (~10%), latency/QPS and recall@10 against brute force over
   the matching subset. Filter strategy and `flatSearchCutoff` (40000) are defaults, so a
   filter matching fewer objects than that is answered by a flat scan of the matches.

HNSW is set explicitly and identically in all three examples: `M=16` (`maxConnections`),
`ef_construction=128`, search `ef=64`. Defaults: `N=100000`; `SMOKE=1` uses `N=10000`; override
with `N`, `DIM`, `K` (e.g. `make benchmark N=1000000`). Results print as a table and are
written to `results/weaviate-<timestamp>.json` (git-ignored) with the Weaviate version,
parameters, and the CPU count and memory of the Docker VM.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `weaviate`
container at `BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update`, and restores the
old limits afterwards. Docker cannot remove a memory limit from a running container, so
"unlimited" comes back as the Docker VM's total memory; `make down && make up` starts clean.
The bench client has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records the applied
limits under `limits`. Weaviate is built with Go 1.26, whose runtime re-reads the cgroup CPU
limit, so `GOMAXPROCS` follows the cap. `GOMEMLIMIT` / `LIMIT_RESOURCES` are not set, so the Go
GC does not know about the 6 GB cap. That is harmless at this size (~0.4 GB used).

### Sample results

2026-09-28, `make benchmark` (N=100000, dim 128), Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64, native image), Weaviate 1.39.7 capped at 4 CPUs / 6 GB,
client 2 CPUs.

| metric | value |
| --- | --- |
| insert (vec/s) | 28,332 (3.5 s) |
| time to queryable (s) | 14.0 (async index queue drained) |
| search, 1 client: QPS / p50 / p99 (ms) | 1,658 / 0.58 / 1.01 |
| search, 8 clients: QPS / p50 / p99 (ms) | 5,796 / 1.27 / 2.84 |
| filtered (~10%), 1 client: QPS / p50 / p99 (ms) | 988 / 0.96 / 1.96 |
| recall@10 / filtered recall@10 | 0.221 / 1.000 |

Weaviate has the best concurrent search throughput of the three vector examples, with a tight
p99 under 8 clients. Recall is low, though: unfiltered recall@10 on uniform random 128-d vectors
at `ef=64` falls to 0.22, compared with Qdrant's 0.59 on the same data. The filtered query is
exact because the ~10k matching vectors fall under the flat-search cutoff, and that is also why
it is slower.
