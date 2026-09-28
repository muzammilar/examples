# YugabyteDB

Website: https://www.yugabyte.com/

Each example exercises both APIs: YSQL (PostgreSQL, port 5433) and YCQL (Cassandra, port 9042).

- [`single-node/`](single-node) — one node via `yugabyted` on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three `yugabyted` nodes, RF=3, one per zone, with Prometheus and Grafana.
- [`kubernetes-helm/`](kubernetes-helm) — 3 masters + 3 tservers on kind with the official `yugabytedb/yugabyte` Helm chart.

## Benchmark

`ysql_bench` (pgbench) on the 3-node RF=3 cluster, 2 CPUs / 4 GB per node (Apple M4 Pro, Docker VM aarch64, 2026-09-28): select-only 19.8k TPS with 16 clients (p99 2.2 ms), TPC-B 275 TPS with 1 client (3.6 ms) and ~730 TPS with 8–16, with no retries. Reads scale, but a distributed write transaction costs ~13x a read and saturates the capped nodes early. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
