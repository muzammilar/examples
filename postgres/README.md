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
| [Citus recovery](citus-docker-compose-cluster/#measured-results) | 2026-11-16, Apple M4 Pro, native aarch64 Docker; each PostgreSQL copy 1 CPU / 1 GiB | Final million-order append: 7.283 s, 137,309 orders/s, batch p99 16.304 ms; group promotions 18.990–21.450 s; live 3 → 5 → 3 moves, 14,318,300 retained ACK orders and witnessed profiles, 0 loss/corruption/misplacement |
| [PgDog pooling and recovery](pgdog-docker-compose-cluster/#measured-results) | 2026-11-16, Apple M4 Pro, native aarch64 Docker; PostgreSQL/PgDog each 1 CPU / 1 GiB | Final million-order append: 6.375 s, 156,865 orders/s, batch p99 16.540 ms; five live fault/config cases, 12 million historical ACKs retained on all three copies with no loss/corruption/duplicates; promotion 20.624 s |
| [Customer sharding](customer-sharding/#results), 2026-11-16 | 8,030,000 retained ACK orders across 11 histories, 10,000 customers, 3 × 3 database copies | Post-recovery million-order append: 6.062 s, 164,973 orders/s, batch p99 15.010 ms. Primary failures and synchronous-standby pauses on every shard; native promotion 17.866–19.354 s, 0 missing/corrupt/duplicate/misplaced rows on all nine copies. |
| [pgvector retrieval](pgvector-product-search/#benchmark) | 2026-11-15, Apple M4 Pro / native aarch64 Docker; 100,000 products, primary 2 CPUs / 2 GiB | 8-client dense 290.82 QPS, p99 86.29 ms, recall@10 0.9919; 20.61 s client-observed write recovery, 0 acknowledged loss |

These dated per-example runs use different datasets and resource budgets; they are not a normalized cross-engine comparison.

## Representative workloads

[Colocated tenant dashboards](citus-docker-compose-cluster/#witnessed-tenant-dashboards-and-profiles) run in `make test`: native plans show one routed task versus 30 global tasks, and 200 mixed requests include 40 witnessed profile transactions. On the final 14,318,300-order dataset, tenant-filtered query p50 was 1.327 ms versus 790.540 ms when fetching every tenant's aggregate to answer one tenant. These short cache-affected observations illustrate avoided fan-out; the profile/order histories are also verified after failover and shard moves.

[Short-lived connection bursts](pgdog-docker-compose-cluster/#representative-workload-connection-bursts) run automatically in `make test`. On the retained 12-million-order dataset, 256 clients opened 4,000 fresh connections per path: PgDog served all 4,000, while direct PostgreSQL served 3,537 with 463 capacity errors. Successful throughput was 1,965 versus 180 requests/s in this capped, fixed-order local burst. Full successful/failed latencies, backend samples and limits are in the example; these are admission observations, not cold-query or capacity rankings.

The existing customer-orders example covers tenant-scoped PostgreSQL transactions and joins through PgDog across three replicated shards. Its million-order runs exercise the customer-local SQL model and recovery; a separate [PgDog pooling scenario](pgdog-docker-compose-cluster/) covers short-lived connection bursts, and [Citus tenant analytics](citus-docker-compose-cluster/#witnessed-tenant-dashboards-and-profiles) covers native distributed-query planning. Those workloads are kept distinct rather than copying the same order writer into another directory.

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

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

| Example | Next work |
|---|---|
| [customer-sharding](customer-sharding/README.md#future-work--todo) | Exercise peer partitions, DCS/quorum loss and stale-primary fencing separately for every shard. |
| [pgdog-docker-compose-cluster](pgdog-docker-compose-cluster/README.md#future-work--todo) | Exercise DCS/quorum loss, peer partitions and stale-primary fencing while independent pooled readers/writers remain live. |
| [citus-docker-compose-cluster](citus-docker-compose-cluster/README.md#future-work--todo) | Partition coordinator/worker replication and lose DCS quorum. |
| [pgvector-product-search](pgvector-product-search/README.md#future-work--todo) | Exercise replication partitions, quorum loss and primary fencing with continued readers and external acknowledged-version histories. |
| [neon/branching-recovery](neon/branching-recovery/README.md#future-work--todo) | Bring supported pageserver/S3 outage, WAL-quorum-loss and restore cases from the Kubernetes scenario into this Compose deployment. |
| [neon/kubernetes-statefulset](neon/kubernetes-statefulset/README.md#future-work--todo) | Test broker outages and peer partitions separately from the already executed pageserver/S3/WAL-quorum cases. |
| [neon/single-node](neon/single-node/README.md#future-work--todo) | Qualify graceful whole-stack stop/start and forced cold reopen with retained storage and complete historical readback. |
