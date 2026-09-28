# OceanBase

Website: https://en.oceanbase.com/

The examples use the MySQL-mode user tenant `test` (port 2881); cluster-wide views are
read from the `sys` tenant.

- [`single-node/`](single-node) — one observer from the `oceanbase/oceanbase-ce` image (`MODE=mini`) on Docker Compose.

- [`docker-compose-cluster/`](docker-compose-cluster) — three observers in three zones (zone1–3) on Docker Compose,
  bootstrapped by hand without obd; tenant `test` has locality `F@zone1, F@zone2, F@zone3`, with a
  `make failover` that kills the leader's observer.

The cluster needs about 20 GB of Docker memory (3 × 6G `memory_limit` plus overhead) and ~20 GB of
disk. OceanBase's own recommendations for clusters are `obd` or
[ob-operator](https://github.com/oceanbase/ob-operator); ob-operator 2.3.4 refuses observers below
8Gi memory and 30Gi data + 30Gi redo storage each, which does not fit on a laptop.

## Benchmark

sysbench on one `mini` observer, 4 CPUs / 8 GB (memory raised from the usual 6 GB, because the observer's `memory_limit` floor is 6G; Apple M4 Pro, Docker VM aarch64, 2026-09-28): point selects 82k/s at p95 0.6 ms with 32 threads, `oltp_read_only` 4.6k tps (74k qps). `oltp_read_write` peaks at ~960 tps with 8 threads and halves at 32. Reads scale on the capped CPUs, while writes saturate early. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).

Cluster (3 observers, 2 CPUs / 7 GB each, all leaders in zone1, sysbench at 32 threads): point selects
40k/s, `oltp_read_write` ~900 tps. Killing the leader's observer moved leadership to zone2 and the
next write committed ~4.6 s after the kill. Full tables: [`docker-compose-cluster/README.md`](docker-compose-cluster/README.md#benchmark).
