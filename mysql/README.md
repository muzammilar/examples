# MySQL ecosystem

Website: [mysql.com](https://www.mysql.com/)

GitHub: [mysql/mysql-server](https://github.com/mysql/mysql-server), [vitessio/vitess](https://github.com/vitessio/vitess)

| Example | Demonstrates |
|---|---|
| [Vitess deployment](vitess-docker-compose-cluster/) | One unsharded keyspace, 1 primary + 2 replicas, semi-sync durability, VTGate and VTOrc recovery |
| [Customer sharding](customer-sharding/) | Vitess 23.0.0, 3 customer shards × 3 copies, colocated writes, primary loss on every shard and acknowledged-ID reconciliation |
| [NDB Cluster](ndb-cluster-docker-compose-cluster/) | Community 8.4.11, 3 partitions with 3 copies each, active-write data/SQL/management failures, acknowledged-data reconciliation and comparison with Vitess |

| Example | Date / setup | Observed result |
|---|---|---|
| [Vitess recovery](vitess-docker-compose-cluster/#measured-results) | 2026-11-14, Apple M4 Pro, amd64 emulated on shared aarch64 Docker | 1,000,000 orders in 269.770 s; 3,707 orders/s; batch p99 1,592.595 ms; promotion 4 s; 0 acknowledged loss |
| [Customer sharding recovery](customer-sharding/#measured-results) | 2026-11-14, Apple M4 Pro, amd64 emulated on shared aarch64 Docker | Fresh baseline: 1,000,000 orders in 132.563 s, 7,544 orders/s, p99 1,288.694 ms; promotions 3–5 s; 0 acknowledged loss |
| [NDB recovery](ndb-cluster-docker-compose-cluster/#measured-results) | 2026-11-14, Apple M4 Pro, amd64 emulated on shared aarch64 Docker | Baseline: 1,000,000 orders in 76.834 s, 13,015 orders/s, p99 99.077 ms; isolated faults lost 0 acknowledged IDs; full-disk crash lost 6,800 of 449,100 |

## Representative workloads

The separate [customer-sharding example](customer-sharding/) already demonstrates Vitess's tenant-oriented workload: 10,000 customers, 1,000,000 orders, a common hash vindex for colocated records, and live primary failures on each of three shards. All three runs reconciled every acknowledged ID, with promotions in 3–5 s. It is the workload example for Vitess/MySQL; the unsharded deployment here remains the topology baseline. [VSchema routing](https://vitess.io/docs/23.0/reference/features/vschema/) and the [23.0.0 hash-vindex source](https://github.com/vitessio/vitess/blob/v23.0.0/go/vt/vtgate/vindexes/hash.go) explain the key-to-shard path.

This existing customer-orders workload fills the Vitess/MySQL workload requirement: tenant-local batches and joins through a common hash vindex, three independent MySQL shards, and recovery under continuing traffic. It retains relational transactions within a customer's shard. Cross-customer distributed transactions and online resharding are different scenarios and remain outside this example. No second copy of the same workload is added. See [VSchema routing](https://vitess.io/docs/23.0/reference/features/vschema/) and the [23.0.0 hash-vindex implementation](https://github.com/vitessio/vitess/blob/v23.0.0/go/vt/vtgate/vindexes/hash.go).

[Subscriber profiles and quota checks](ndb-cluster-docker-compose-cluster/#representative-workload-subscriber-profiles-and-online-quota-checks) run in `make test`: 1,000,000 profiles, primary/unique-hash lookups and atomic quota updates. Primary lookup p50 was 0.248 ms versus 57.793 ms for an unindexed reference scan; 2,000 acknowledged quota updates reconciled exactly. The example also documents the cost of scans and the separate correlated disk-full loss.

## Known issues

The pinned image requires amd64 emulation on Apple Silicon. All local copies share one host and disk. The deployment example fixes one unsharded keyspace; customer sharding belongs in its separate example.

The pinned Vitess image requires amd64 emulation on Apple Silicon. All local copies share one host and disk. The dedicated customer-sharding example fixes three shards; online resharding is not implemented.

The NDB image requires amd64 emulation on Apple Silicon. All local replicas share the host disk; process-failure success does not establish storage-failure durability. See the NDB README for the measured correlated disk-full loss and exact configuration.

Existing clients also live under [Go](../go/), [Python](../python/mysql-client/) and [ClickHouse named collections](../clickhouse/named-collection-mysql/).
