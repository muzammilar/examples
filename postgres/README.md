# PostgreSQL ecosystem

Website: [postgresql.org](https://www.postgresql.org/), [citusdata.com](https://www.citusdata.com/)

GitHub: [postgres/postgres](https://github.com/postgres/postgres), [citusdata/citus](https://github.com/citusdata/citus)

| Example | Demonstrates |
|---|---|
| [Citus cluster](citus-docker-compose-cluster/) | 3 worker groups × 3 copies plus 3 coordinator copies; colocated orders, worker/coordinator failover, replica stalls and scaling worker groups 3 → 5 → 3 |

| Example | Date / setup | Observed result |
|---|---|---|
| [Citus recovery](citus-docker-compose-cluster/#measured-results) | 2026-11-14, Apple M4 Pro, shared native aarch64 Docker | 1,000,000 orders in 4.735 s; 211,185 orders/s; batch p99 16.857 ms; promotions 21 s; 0 acknowledged loss |

## Representative workload

[Colocated tenant dashboards](citus-docker-compose-cluster/#representative-workload-tenant-dashboards) run in the cluster example's `make test`: 1,000,000 orders, 10,000 tenants, one routed task versus 30 distributed tasks, and 40 committed profile transactions. Measured tenant-query p50 was 0.438 ms versus 73.078 ms when fetching every tenant's aggregate for a single customer's answer. This is the Citus workload example; no duplicate deployment is needed.

## Known issues

Citus is built with matching PostgreSQL runtime and development packages. Patroni handles physical-copy promotion; Citus handles distributed-table placement. Scale-in must finish deferred shard cleanup before removing a group. All containers share one Docker disk and host, including the 18 PostgreSQL instances during scale-out.

Older PostgreSQL examples remain under [Kubernetes](../k8s/hello-postgres/) and [Vagrant](../vagrant/). PgDog pooling and customer sharding are separate scenarios.
