# MySQL ecosystem

Website: [mysql.com](https://www.mysql.com/)

GitHub: [mysql/mysql-server](https://github.com/mysql/mysql-server), [vitessio/vitess](https://github.com/vitessio/vitess)

| Example | Demonstrates |
|---|---|
| [Vitess deployment](vitess-docker-compose-cluster/) | One unsharded keyspace, 1 primary + 2 replicas, semi-sync durability, VTGate and VTOrc recovery |

| Example | Date / setup | Observed result |
|---|---|---|
| [Vitess recovery](vitess-docker-compose-cluster/#measured-results) | 2026-11-14, Apple M4 Pro, amd64 emulated on shared aarch64 Docker | 1,000,000 orders in 269.770 s; 3,707 orders/s; batch p99 1,592.595 ms; promotion 4 s; 0 acknowledged loss |

## Representative workload

The separate [customer-sharding example](customer-sharding/) already demonstrates Vitess's tenant-oriented workload: 10,000 customers, 1,000,000 orders, a common hash vindex for colocated records, and live primary failures on each of three shards. All three runs reconciled every acknowledged ID, with promotions in 3–5 s. It is the workload example for Vitess/MySQL; the unsharded deployment here remains the topology baseline. [VSchema routing](https://vitess.io/docs/23.0/reference/features/vschema/) and the [23.0.0 hash-vindex source](https://github.com/vitessio/vitess/blob/v23.0.0/go/vt/vtgate/vindexes/hash.go) explain the key-to-shard path.

## Known issues

The pinned image requires amd64 emulation on Apple Silicon. All local copies share one host and disk. The deployment example fixes one unsharded keyspace; customer sharding belongs in its separate example.

Existing clients also live under [Go](../go/), [Python](../python/mysql-client/) and [ClickHouse named collections](../clickhouse/named-collection-mysql/).
