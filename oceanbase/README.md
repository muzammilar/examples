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
