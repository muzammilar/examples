# MySQL ecosystem

Website: [mysql.com](https://www.mysql.com/)

GitHub: [mysql/mysql-server](https://github.com/mysql/mysql-server), [vitessio/vitess](https://github.com/vitessio/vitess)

| Example | Demonstrates |
|---|---|
| [Vitess deployment](vitess-docker-compose-cluster/) | One unsharded keyspace, 1 primary + 2 replicas, semi-sync durability, VTGate and VTOrc recovery |
| [Customer sharding](customer-sharding/) | Vitess 23.0.0, 3 customer shards × 3 copies, preserved ACK histories, independent reads, every-shard primary/pause tests and full-body native-copy verification |
| [NDB Cluster](ndb-cluster-docker-compose-cluster/) | Community 8.4.11, 3 partitions with 3 copies each, active-write data/SQL/management failures, acknowledged-data reconciliation and comparison with Vitess |

| Example | Date / setup | Observed result |
|---|---|---|
| [Vitess recovery](vitess-docker-compose-cluster/#measured-results) | 2026-11-14, Apple M4 Pro, amd64 emulated on shared aarch64 Docker | 1,000,000 orders in 269.770 s; 3,707 orders/s; batch p99 1,592.595 ms; promotion 4 s; 0 acknowledged loss |
| [Customer sharding recovery](customer-sharding/#measured-results) | 2026-11-16, Apple M4 Pro, amd64 emulated on capped shared aarch64 Docker | Eight live fault cases + baseline/benchmark retain 10,252,200 ACK orders on all nine copies; post-recovery million: 33.171 s, 30,147 orders/s, successful-attempt p99 93.086 ms; promotions 3.452–4.826 s; 0 acknowledged loss |
| [NDB recovery](ndb-cluster-docker-compose-cluster/#measured-results) | 2026-11-14, Apple M4 Pro, amd64 emulated on shared aarch64 Docker | Baseline: 1,000,000 orders in 76.834 s, 13,015 orders/s, p99 99.077 ms; isolated faults lost 0 acknowledged IDs; full-disk crash lost 6,800 of 449,100 |

## Representative workloads

The [customer-sharding example](customer-sharding/) demonstrates Vitess's tenant
workload: 10,000 customers, a shared `hash` vindex for customer-local transactions
and three independent MySQL shards with three copies each. The retained-history
suite tests primary crashes and replica pauses on every shard, plus replica and
router crashes, while independent reads/writes continue. Native vindex output
establishes ownership, and every historical row is checked through VTGate and on
all nine physical copies. Cross-customer distributed transactions and online
resharding remain separate, unrun scenarios. See [VSchema
routing](https://vitess.io/docs/23.0/reference/features/vschema/) and the
[pinned hash-vindex source](https://github.com/vitessio/vitess/blob/v23.0.0/go/vt/vtgate/vindexes/hash.go).

[Subscriber profiles and quota checks](ndb-cluster-docker-compose-cluster/#representative-workload-subscriber-profiles-and-online-quota-checks) run in `make test`: 1,000,000 profiles, primary/unique-hash lookups and atomic quota updates. Primary lookup p50 was 0.248 ms versus 57.793 ms for an unindexed reference scan; 2,000 acknowledged quota updates reconciled exactly. The example also documents the cost of scans and the separate correlated disk-full loss.

## Known issues

The pinned Vitess image requires amd64 emulation on Apple Silicon. All local copies share one host and disk. The deployment example fixes one unsharded keyspace; the separate customer-sharding example fixes three shards. Online resharding is not implemented.

The NDB image requires amd64 emulation on Apple Silicon. All local replicas share the host disk; process-failure success does not establish storage-failure durability. See the NDB README for the measured correlated disk-full loss and exact configuration.

Existing clients also live under [Go](../go/), [Python](../python/mysql-client/) and [ClickHouse named collections](../clickhouse/named-collection-mysql/).
