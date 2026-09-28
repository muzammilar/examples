# YDB

Website: https://ydb.tech/

- [`single-node/`](single-node) — `local-ydb`: storage and database `/local` in one container with in-memory disks, on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — 3 storage nodes (one per zone, `mirror-3-dc`) and 2 dynamic nodes serving `/Root/testdb`, with Prometheus and Grafana and a `make failover` that stops a zone and a dynamic node.
- [`kubernetes-operator/`](kubernetes-operator) — a `mirror-3-dc` `Storage` and a `Database` on a multi-zone kind cluster, managed by the YDB Kubernetes Operator (Helm).

The `ydbplatform/local-ydb` image is amd64 only, so on Apple silicon `ydbd` runs under emulation.

## Benchmark

YDB CLI workloads on 3 storage + 2 dynamic nodes, 6 CPUs / 12 GB (Apple M4 Pro, Docker VM aarch64, **amd64 `ydbd` under emulation**, 2026-09-28): one-row upserts reach 5.8k/s (p50 3 ms) and point selects 8.4k/s; the serializable multi-table order transaction saturates at ~580/s, where extra threads only add lock-conflict retries. Under load, stopping a whole zone caused no dip and no errors, stopping a dynamic node failed 11 of 679k requests, and the self-check was GOOD again ~20 s after restart. TPC-C held 100% efficiency at 10 warehouses, and on the column store TPC-H Q1 ran 3.7x faster than on rows. The price of distributed ACID is ~2–3 ms per single-row write. Range reads (1M-row composite-key table, serializable): a 100-row primary-key range takes 1.4 ms p50 and peaks at ~2.5k queries/s (250k rows/s), a range via a global secondary index costs up to ~2x the latency, and a 10k-row COUNT/SUM aggregates 10M rows/s. Full tables, extended benchmark and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
