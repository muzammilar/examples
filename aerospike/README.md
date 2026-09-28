# Aerospike

Website: https://aerospike.com/

All examples run Aerospike Community Edition with namespace `test` in memory.

- [`single-node/`](single-node) — one node from the stock `aerospike/aerospike-server` image with its built-in config, on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three nodes over mesh heartbeats, `replication-factor 2`, a Prometheus exporter per node, Prometheus and Grafana.
- [`kubernetes/`](kubernetes) — three nodes on kind as a plain `StatefulSet` with a headless `Service` for the mesh seeds (the Aerospike Kubernetes Operator targets Enterprise Edition).

## Benchmark

`asbench` against the 3-node RF=2 cluster, 2 CPUs / 4 GB per node (Apple M4 Pro, Docker VM aarch64, 2026-09-28): reads 186k ops/s at p99 0.15 ms, inserts 75k ops/s at p99 0.44 ms, 80/20 read-update 135k reads + 34k writes/s. Reads are local and sub-millisecond; a write waits for its replica, so it costs ~5x a read at p50 and has a 15–34 ms p99.9 tail on the capped CPUs. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
