# ScyllaDB

Website: https://www.scylladb.com/

- [`single-node/`](single-node) — one ScyllaDB node (1 shard, 1 GiB, developer mode) on Docker Compose, CQL on localhost.
- [`docker-compose-cluster/`](docker-compose-cluster) — three nodes (1 shard, 750 MiB each), RF=3, with Prometheus and Grafana on ScyllaDB's metrics endpoint.
- [`kubernetes-operator/`](kubernetes-operator) — a 3-member `ScyllaCluster` on kind managed by ScyllaDB Operator (Helm), with `ScyllaDBMonitoring`.

Every ScyllaDB shard reserves ~11k Linux AIO contexts, more than Docker Desktop's default
`fs.aio-max-nr` (65536) allows for a few nodes. The compose examples start a privileged one-shot
`sysctl` service that raises it to 1048576 before ScyllaDB starts, and `kubernetes-operator/`
does the same with [`sysctl-daemonset.yaml`](kubernetes-operator/sysctl-daemonset.yaml). The
setting is not namespaced: on Docker Desktop it applies to the whole Docker VM until Docker
restarts (OceanBase on the same VM needs it too), and on a Linux host it changes the host kernel.

## Benchmark

cassandra-stress against the 3-node RF=3 cluster at CL=QUORUM, 2 CPUs / 4 GB per node, with each node running 1 shard / 750 MB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): writes 52.7k ops/s (p50 0.3 ms), reads 40.3k ops/s (p99 2.2 ms), and an LWT update 15.5k ops/s against 59.5k for a plain update. An LWT costs ~6x a plain write at p50, and writes beat QUORUM reads. Full table and method: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
