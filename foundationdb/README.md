# FoundationDB

Website: https://www.foundationdb.org/

All examples use the Redwood storage engine (`ssd-redwood-1`).

- [`single-node/`](single-node) — one `fdbserver` process, `single` redundancy, on Docker Compose.
- [`docker-compose-cluster/`](docker-compose-cluster) — three `fdbserver` containers in `double` redundancy, all coordinators, with foundationdb-exporter, Prometheus and Grafana.
- [`kubernetes-operator/`](kubernetes-operator) — a `double`-redundancy `FoundationDBCluster` on kind managed by the FoundationDB Kubernetes Operator.

## Benchmark

Python-client transactions against the 3-process `double` cluster, 2 CPUs / 4 GB per process (Apple M4 Pro, Docker VM aarch64, 2026-09-28): point reads 3.4k tx/s at p50 2.1 ms, blind writes 1.9k tx/s at p50 4.0 ms, and batching gets 157k keys/s on load and 249k keys/s on range reads. Latency comes from commit round trips rather than CPU. On 100 hot keys, read-modify-write conflicts 12.6% of the time (p99 31 ms), while atomic adds never do. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
