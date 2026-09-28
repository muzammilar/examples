# Milvus — standalone

Milvus standalone (all roles in one process) with its two dependencies, trimmed from
upstream's `milvus-standalone-docker-compose.yaml`: etcd for metadata, MinIO for segment and
index files. `make test` runs a short pymilvus script from a `tools` container.

```bash
make up       # start etcd, MinIO and Milvus, wait for /healthz
make test     # run scripts/test.py: collection, insert + flush, HNSW index + load, k-NN, expr filter, range search
make status   # container health and collections (REST)
make cli      # python REPL with a connected MilvusClient as `client`
make benchmark [SMOKE=1]  # insert / index / search / recall benchmark (bench/bench.py, needs `make up`)
make down     # remove containers, volumes and the locally built tools image
```

- gRPC + REST: `localhost:19530` (e.g. `curl -X POST localhost:19530/v2/vectordb/collections/list -d '{}'`)
- Web UI: http://localhost:9091/webui
- Health / metrics: http://localhost:9091/healthz, http://localhost:9091/metrics

MinIO (`minioadmin`/`minioadmin`) and etcd are not published to the host. No auth on Milvus;
its ports are bound to localhost because there is no auth. The Milvus image is a ~600 MB
download (2.2 GB on disk); healthy in 10–60 s. A collection must be indexed and loaded before
it can be searched; `make test` drops and recreates `landmarks`.

## Benchmark

`make benchmark` runs [`bench/bench.py`](bench/bench.py) in the official uv image
(`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`, deps pinned in `bench/uv.lock`, no
host Python needed). The uv cache and venv sit in the `uv-cache` volume, so reruns start in
seconds; it belongs to the `bench` profile, so `make down` keeps it
(`docker volume rm milvus-single_uv-cache` drops it). It uses pymilvus (gRPC on `standalone:19530`).
The workload is the same as in [`../../qdrant/single-node`](../../qdrant/single-node) and
[`../../weaviate/single-node`](../../weaviate/single-node), so results compare:

1. **Insert**: N seeded random unit vectors (float32, dim 128, cosine) with an INT64 field
   `tag` in [0, 10), inserted in batches of 1000 into the unindexed collection `bench`, one client.
2. **Time to queryable**: `flush` (seal segments into MinIO), `create_index` (HNSW on
   `vec`, INVERTED on `tag`) and wait until every row is indexed, then `load_collection`;
   each phase is reported.
3. **Search**: 1000 k=10 queries, p50/p95/p99 latency and QPS with 1 client, then 8
   concurrent clients (one connection each), default Bounded consistency.
4. **Recall@10** of the first 200 queries against exact numpy brute force.
5. **Filtered search**: `tag == 0` (~10%), latency/QPS and recall@10 against brute force over
   the matching subset (HNSW search with the filter applied as a bitset).

HNSW is set explicitly and identically in all three examples: `M=16`, `efConstruction=128`,
search `ef=64`. Defaults: `N=100000`; `SMOKE=1` uses `N=10000`; override with `N`, `DIM`, `K`
(e.g. `make benchmark N=1000000`). Results print as a table and are written to
`results/milvus-<timestamp>.json` (git-ignored) with the Milvus version, parameters, and the
CPU count and memory of the Docker VM.

### Sample results

TODO: fill from a run on a quiet machine (`make benchmark`, N=100000, dim 128).

| metric | value |
| --- | --- |
| insert (vec/s) | TODO |
| time to queryable (s): flush / index / load | TODO |
| search, 1 client: QPS / p50 / p99 (ms) | TODO |
| search, 8 clients: QPS / p50 / p99 (ms) | TODO |
| filtered (~10%), 1 client: QPS / p50 / p99 (ms) | TODO |
| recall@10 / filtered recall@10 | TODO |
