# YugabyteDB

Website: https://www.yugabyte.com/

Each example exercises both APIs: YSQL (PostgreSQL, port 5433) and YCQL (Cassandra, port 9042).

- [`single-node/`](single-node) — one node via `yugabyted` on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three `yugabyted` nodes, RF=3, one per zone, with Prometheus and Grafana.
- [`kubernetes-helm/`](kubernetes-helm) — 3 masters + 3 tservers on kind with the official `yugabytedb/yugabyte` Helm chart.
