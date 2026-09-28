# Milvus

Website: https://milvus.io/

- [`single-node/`](single-node) — Milvus standalone (with etcd and MinIO) on Docker Compose: HNSW and inverted indexes, k-NN, filtered and range search via pymilvus.
- [`single-node-rustfs/`](single-node-rustfs) — the same standalone setup with RustFS (Apache-2.0, S3-compatible) in place of MinIO for object storage.

## Benchmark

Random-vector benchmark (100k × 128-d, HNSW M=16/efC=128/ef=64) on 4 CPUs / 6 GB for standalone + etcd + object store (Apple M4 Pro, Docker VM aarch64, 2026-09-28). Milvus 3.0.2 ingests at ~110k vec/s but needs ~20 s of flush, index build and load before the first query. After that it serves ~925 QPS with 1 client (p99 ~2 ms) and ~3.3k QPS with 8. MinIO and RustFS give the same numbers within noise, because the object store is only on the flush/load path. Full tables and method: [`single-node/README.md`](single-node/README.md#benchmark), [`single-node-rustfs/README.md`](single-node-rustfs/README.md#benchmark).
