# RonDB

Website: https://www.rondb.com/

- [`docker-compose-cluster/`](docker-compose-cluster) — the minimal cluster from `rondb-docker`: 1 management server, 2 data nodes (1 node group, 2 replicas), 1 MySQL Server and the REST API server, with a `make failover` that stops a data node.

There is no single-node example: RonDB always runs as separate processes (management
server, data nodes, MySQL Server). rondb-docker's smallest `mini` profile still starts
those processes with one data node (`NoOfReplicas=1`), which only drops the redundancy
this cluster demonstrates.
