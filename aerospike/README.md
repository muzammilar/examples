# Aerospike

Website: https://aerospike.com/

All examples run Aerospike Community Edition with namespace `test` in memory.

- [`single-node/`](single-node) — one node from the stock `aerospike/aerospike-server` image with its built-in config, on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three nodes over mesh heartbeats, `replication-factor 2`, a Prometheus exporter per node, Prometheus and Grafana.
- [`kubernetes/`](kubernetes) — three nodes on kind as a plain `StatefulSet` with a headless `Service` for the mesh seeds (the Aerospike Kubernetes Operator targets Enterprise Edition).

## Benchmark

`asbench` against the 3-node RF=2 cluster, 2 CPUs / 4 GB per node (Apple M4 Pro, Docker VM aarch64, 2026-09-28): reads 186k ops/s at p99 0.15 ms, inserts 75k ops/s at p99 0.44 ms, 80/20 read-update 135k reads + 34k writes/s. Reads are local and sub-millisecond; a write waits for its replica, so it costs ~5x a read at p50 and has a 15–34 ms p99.9 tail on the capped CPUs. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).

Lua UDFs vs native ops ([`go/lua-bench/aerospike`](../go/lua-bench/aerospike), the rueidis-lua-bench workload on the Aerospike wire protocol instead of RESP), single node capped at 2 CPUs, `-n 1000000 -c 50 -keys 100000`, 2026-10-04: put 150k, get 169k ops/s; add/update/delete as Lua record UDFs 104k / 88k / 112k ops/s, as native ops (`CREATE_ONLY` put, `EXPECT_GEN_EQUAL` operate, delete) 132k / 90k / 96k ops/s, p99 under 7 ms. Only the create-only add gains clearly from going native. Full table: [`go/lua-bench/aerospike/README.md`](../go/lua-bench/aerospike/README.md#results).
