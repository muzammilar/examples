# Weaviate

Website: https://weaviate.io/

- [`single-node/`](single-node) — one Weaviate server with Docker Compose and self-supplied vectors: nearVector, filtered search, BM25 and hybrid search over GraphQL.

## Benchmark

Random-vector benchmark (100k × 128-d, HNSW M=16/efC=128/ef=64) on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): insert 28k vec/s and queryable after 14 s (async indexing); search 1,658 QPS with 1 client and 5,796 QPS at p99 2.8 ms with 8. Concurrency scales well, but unfiltered recall@10 on this hard random data is only 0.22 at `ef=64`. The ~10% filter is exact (flat search) and costs about 2x latency. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
