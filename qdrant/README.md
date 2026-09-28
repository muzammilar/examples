# Qdrant

Website: https://qdrant.tech/

- [`single-node/`](single-node) — one Qdrant server with Docker Compose: collection, payload index, k-NN, filtered search and the recommend API over REST.
- [`docker-compose-cluster/`](docker-compose-cluster) — three Qdrant peers in distributed mode (Raft): a 3-shard, 2-replica collection, shard placement, and a failover demo (stop a peer, search and upsert keep working, its replicas recover to Active).

## Benchmark

Random-vector benchmark (100k × 128-d, HNSW M=16/efC=128/ef=64) on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): insert 53k vec/s and queryable after 10.6 s; search 1,419 QPS at p99 0.9 ms with 1 client and 3,209 QPS with 8. A `tag` filter (~10%) is faster (1,979 QPS) and more accurate (recall 0.945 vs 0.591) than unfiltered search, because the payload index narrows the candidates. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
