# OceanBase

Website: https://en.oceanbase.com/

The examples use the MySQL-mode user tenant `test` (port 2881); cluster-wide views are
read from the `sys` tenant.

- [`single-node/`](single-node) — one observer from the `oceanbase/oceanbase-ce` image (`MODE=mini`) on Docker Compose.

There is no multi-observer example. OceanBase recommends `obd` or
[ob-operator](https://github.com/oceanbase/ob-operator) on Kubernetes for clusters, and a
3-zone cluster does not fit on a laptop. ob-operator 2.3.4 refuses an `OBCluster` with less
than 8Gi memory, 30Gi data storage or 30Gi redo-log storage per observer, and it
preallocates 80% of the redo-log volume as the log disk. Three observers therefore need
24 GiB of RAM (plus kind and the operator), which is the whole Docker VM here, and at
least 72 GiB of preallocated log disk. Even at the image's own 6G `memory_limit` floor,
three observers would need 18 GiB.

## Benchmark

sysbench on one `mini` observer, 4 CPUs / 8 GB (memory raised from the usual 6 GB, because the observer's `memory_limit` floor is 6G; Apple M4 Pro, Docker VM aarch64, 2026-09-28): point selects 82k/s at p95 0.6 ms with 32 threads, `oltp_read_only` 4.6k tps (74k qps). `oltp_read_write` peaks at ~960 tps with 8 threads and halves at 32. Reads scale on the capped CPUs, while writes saturate early. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
