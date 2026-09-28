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
