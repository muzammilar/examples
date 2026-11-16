# MySQL ecosystem

Website: [mysql.com](https://www.mysql.com/)

GitHub: [mysql/mysql-server](https://github.com/mysql/mysql-server), [vitessio/vitess](https://github.com/vitessio/vitess)

| Example | Demonstrates |
|---|---|
| [Vitess deployment](vitess-docker-compose-cluster/) | One unsharded keyspace, 1 primary + 2 replicas, retained histories, independent reads, full-copy oracle and VTGate/VTOrc recovery |
| [Customer sharding](customer-sharding/) | Vitess 23.0.0, 3 customer shards × 3 copies, preserved ACK histories, independent reads, every-shard primary/pause tests and full-body native-copy verification |
| [NDB Cluster](ndb-cluster-docker-compose-cluster/) | Community 8.4.11, 3 partitions × 3 replicas, preserved order/quota histories, independent reads, native FRAGMENT placement and eight live data/SQL/management/router failures |

| Example | Date / setup | Observed result |
|---|---|---|
| [Vitess recovery](vitess-docker-compose-cluster/#measured-results) | 2026-11-16, Apple M4 Pro, amd64 emulated on capped shared aarch64 Docker | Four live fault cases + baseline/benchmark retain 6,184,600 ACK orders on all three copies; post-recovery million: 60.363 s, 16,567 orders/s, successful-attempt p99 191.590 ms; promotion 3.551 s, native repair barrier 69.302 s; 0 acknowledged loss |
| [Customer sharding recovery](customer-sharding/#measured-results) | 2026-11-16, Apple M4 Pro, amd64 emulated on capped shared aarch64 Docker | Eight live fault cases + baseline/benchmark retain 10,252,200 ACK orders on all nine copies; post-recovery million: 33.171 s, 30,147 orders/s, successful-attempt p99 93.086 ms; promotions 3.452–4.826 s; 0 acknowledged loss |
| [NDB recovery](ndb-cluster-docker-compose-cluster/#measured-recovery-results) | 2026-11-16, Apple M4 Pro, amd64 emulated, capped shared aarch64 Docker | Eight live role faults, 11,721,100 retained ACK orders, 1,000,000 profile bodies / 6,000 quota ACKs, 0 reconciliation errors; post-recovery million: 6.512 s, 153,562 orders/s, successful-attempt p99 8.647 ms. Historical correlated disk-full loss: 6,800 of 449,100 ACKs |

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
