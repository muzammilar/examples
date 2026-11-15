# Qdrant

Website: https://qdrant.tech/

- [`single-node/`](single-node) — one Qdrant server with Docker Compose: collection, payload index, k-NN, filtered search and the recommend API over REST.
- [`docker-compose-cluster/`](docker-compose-cluster) — three Qdrant peers in distributed mode (Raft): a 3-shard, 2-replica collection, shard placement, and a failover demo (stop a peer, search and upsert keep working, its replicas recover to Active).

## TurboQuant cluster

[`turboquant/`](turboquant/) compares float32 RAM/disk, scalar int8 and TurboQuant
4-bit on 100,000 × 384-dimensional synthetic vectors per profile. Three customer
shard keys each have three copies. Full-vector/shard checks and four live recovery
cases passed: 19,766 acknowledged recovery points, zero acknowledged loss or wrong
values. Native empty-disk replacement is followed by a different peer crash.

The audited warm benchmark measured TurboQuant unfiltered recall@10 **0.733**
without rescoring and **1.000** with 4× rescoring (p50 **3.562 ms**, **148.7 QPS**
at four concurrent requests). The README has all 16 variants, native memory/disk
accounting, resource budgets and limits; compressed bytes are not whole-process
memory savings. Prior incomplete/unfair benchmark attempts are documented.

TurboQuant quantization is available from **v1.18.0**. In this release it is an
additional quantization layer; do not confuse compressed-vector bytes with total
resident memory or assume the original vectors disappear.
Sources: [1.18 release](https://qdrant.tech/blog/qdrant-1.18.x/),
[quantization configuration](https://qdrant.tech/documentation/manage-data/quantization/).

## Benchmark

Random-vector benchmark (100k × 128-d, HNSW M=16/efC=128/ef=64) on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): insert 53k vec/s and queryable after 10.6 s; search 1,419 QPS at p99 0.9 ms with 1 client and 3,209 QPS with 8. A `tag` filter (~10%) is faster (1,979 QPS) and more accurate (recall 0.945 vs 0.591) than unfiltered search, because the payload index narrows the candidates. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
