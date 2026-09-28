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

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) splits `BENCH_CPUS=4` /
`BENCH_MEM=6g` (no swap) with `docker update`: `etcd` 0.25 CPU / 256 MB, `minio` 0.25 CPU /
512 MB, and the rest (3.5 CPUs / 5.25 GB) to `standalone`, which does all the indexing and
search. The old limits come back afterwards. Docker cannot remove a memory limit from a running
container, so "unlimited" returns as the Docker VM's total memory; `make down && make up` starts
clean. The bench client has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records the
applied limits under `limits`. The limits are set on running containers. Milvus sized its knowhere
and segcore thread pools, and its memory quota, from the VM (11 CPUs, 24 GB) at startup, so it
runs more threads than it has CPU quota. The cgroup still caps the CPU time it gets.

### Sample results

2026-09-28, `make benchmark` (N=100000, dim 128), Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64, native images), Milvus 3.0.2, 4 CPUs / 6 GB split as
above, client 2 CPUs.

| metric | value |
| --- | --- |
| insert (vec/s) | 114,702 (0.9 s) |
| time to queryable (s): flush / index / load | 19.9: 2.5 / 15.2 / 2.2 |
| search, 1 client: QPS / p50 / p99 (ms) | 925 / 1.02 / 1.97 |
| search, 8 clients: QPS / p50 / p99 (ms) | 3,343 / 2.16 / 5.17 |
| filtered (~10%), 1 client: QPS / p50 / p99 (ms) | 645 / 1.48 / 2.30 |
| recall@10 / filtered recall@10 | 0.267 / 0.620 |

Ingest is the fastest of the three vector examples, because inserts only append to the log. The
flush, index build and load before the first query take the longest, though (~20 s). Single
queries pay ~1 ms of proxy/querynode overhead, and recall at `ef=64` on uniform random 128-d
vectors is low, even for the filtered query.
