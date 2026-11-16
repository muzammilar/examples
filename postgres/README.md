# PostgreSQL ecosystem

Website: [postgresql.org](https://www.postgresql.org/), [citusdata.com](https://www.citusdata.com/), [PgDog](https://pgdog.dev/)

GitHub: [postgres/postgres](https://github.com/postgres/postgres), [citusdata/citus](https://github.com/citusdata/citus), [pgdogdev/pgdog](https://github.com/pgdogdev/pgdog)

| Example | Demonstrates |
|---|---|
| [Citus cluster](citus-docker-compose-cluster/) | 3 worker groups × 3 copies plus 3 coordinator copies; colocated orders, worker/coordinator failover, replica stalls and scaling worker groups 3 → 5 → 3 |
| [PgDog pooling](pgdog-docker-compose-cluster/) | Transaction pooling, pool-size reload, explicit replica reads and Patroni primary failover; one primary plus two replicas |
| [pgvector product retrieval](pgvector-product-search/) | Single/three-copy profiles, transactional inventory joins, filtered HNSW/hybrid retrieval and primary-loss reconciliation |
| [Customer sharding](customer-sharding/) | PgDog routes by `customer_id` across three Postgres shards, each with one primary and two replicas; Patroni failover, synchronous-replica stalls and acknowledgement reconciliation. |
| [Neon](neon/) | Native storage/compute separation, single-copy and three-WAL-member deployments, copy-on-write branch isolation and empty WAL/compute-disk recovery; matched PostgreSQL corpus and explicit self-hosted control-plane limits |

| Example | Date / setup | Observed result |
|---|---|---|
| [Citus recovery](citus-docker-compose-cluster/#measured-results) | 2026-11-14, Apple M4 Pro, shared native aarch64 Docker | 1,000,000 orders in 4.735 s; 211,185 orders/s; batch p99 16.857 ms; promotions 21 s; 0 acknowledged loss |
| [PgDog pooling and recovery](pgdog-docker-compose-cluster/#measured-results) | 2026-11-14, Apple M4 Pro, shared native aarch64 Docker | Final 8-client baseline: 1,000,000 orders in 27.267 s, 36,675 orders/s, p99 207.830 ms; promotion 21 s, 0 acknowledged loss |
| [Customer sharding](customer-sharding/#results), 2026-11-16 | 8,030,000 retained ACK orders across 11 histories, 10,000 customers, 3 × 3 database copies | Post-recovery million-order append: 6.062 s, 164,973 orders/s, batch p99 15.010 ms. Primary failures and synchronous-standby pauses on every shard; native promotion 17.866–19.354 s, 0 missing/corrupt/duplicate/misplaced rows on all nine copies. |
| [pgvector retrieval](pgvector-product-search/#benchmark) | 2026-11-15, Apple M4 Pro / native aarch64 Docker; 100,000 products, primary 2 CPUs / 2 GiB | 8-client dense 290.82 QPS, p99 86.29 ms, recall@10 0.9919; 20.61 s client-observed write recovery, 0 acknowledged loss |

## Representative workloads

[Colocated tenant dashboards](citus-docker-compose-cluster/#representative-workload-tenant-dashboards) run in the cluster example's `make test`: 1,000,000 orders, 10,000 tenants, one routed task versus 30 distributed tasks, and 40 committed profile transactions. Measured tenant-query p50 was 0.438 ms versus 73.078 ms when fetching every tenant's aggregate for a single customer's answer. This is the Citus workload example; no duplicate deployment is needed.

[Short-lived connection bursts](pgdog-docker-compose-cluster/#representative-workload-short-lived-connection-bursts) run automatically in `make test`. With 256 clients opening 4,000 fresh connections, PgDog served 4,000 requests with 0 errors; direct PostgreSQL served 2,520 with 1,480 capacity errors. Successful throughput was 3,494 requests/s versus 1,141 requests/s on the same million-order data. This demonstrates transaction-pool admission and backend reuse separately from customer sharding.

The existing customer-orders example covers tenant-scoped PostgreSQL transactions and joins through PgDog across three replicated shards. Its million-order runs exercise the customer-local SQL model and recovery; a separate [PgDog pooling scenario](pgdog-docker-compose-cluster/) covers short-lived connection bursts, and [Citus tenant analytics](citus-docker-compose-cluster/) covers native distributed-query planning. Those workloads are kept distinct rather than copying the same order writer into another directory.

## Known issues

Citus is built with matching PostgreSQL runtime and development packages. Patroni handles physical-copy promotion; Citus handles distributed-table placement. Scale-in must finish deferred shard cleanup before removing a group. All containers share one Docker disk and host, including the 18 PostgreSQL instances during scale-out.

PgDog discovers roles and routes queries; Patroni owns promotion. Replica reads do not provide read-your-writes automatically. All local copies share one physical host and disk. Separate customer-sharding and Citus examples cover different placement models.

Older [Helm](../k8s/hello-postgres/) and [Vagrant](../vagrant/multi-vm-postgres/) deployments remain independent examples.

Older [Helm](../k8s/hello-postgres/) and [Vagrant](../vagrant/multi-vm-postgres/) deployments remain independent examples.

## Vector retrieval

[pgvector inventory-aware product search](pgvector-product-search/) provides
single-node and Patroni three-copy deployments, transactional inventory joins,
filtered ANN, hybrid retrieval, crash recovery and a shared ESCI evaluation.
Both profiles were run against 100,000 products / 1,000 held-out queries. The
example README records measured latency/recall and recovery limits.
