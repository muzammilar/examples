# Shared Iceberg storage with DuckDB writers and ClickHouse cluster

This is a separate example from the engine benchmark in [`../benchmarks/`](../benchmarks/README.md). Its purpose is to test shared-storage interoperability and concurrency, not to compare query-engine speed.

## Target topology

```text
DuckDB writer 1 ─┐                         ┌─ ClickHouse shard 1, replica 1
DuckDB writer 2 ─┼── Iceberg REST catalog ─┤  ClickHouse shard 1, replica 2
...             ┘            │            ├─ ClickHouse shard 2, replica 1
                             │            └─ ClickHouse shard 2, replica 2
                             └── self-hosted S3-compatible storage
ClickHouse Keeper quorum coordinates ClickHouse replica metadata
```

The DuckDB writers are independent processes with disjoint input batches. They commit the same catalog-managed Iceberg table. ClickHouse nodes read the table from shared object storage. The Iceberg catalog coordinates Iceberg commits; ClickHouse Keeper coordinates ClickHouse replicated tables and is a separate service.

## Candidate local stack

- SeaweedFS table bucket for self-hosted S3-compatible storage and its Iceberg REST catalog.
- PostgreSQL-backed Apache Polaris instead of the embedded SeaweedFS catalog if the goal is specifically to compare separate catalog and storage services.
- Four ClickHouse nodes in a 2-shard, 2-replica topology, plus a 3-node ClickHouse Keeper quorum.
- A scalable DuckDB writer service with N independently configured writer IDs.

The first local Compose topology will share one host and is not a production HA deployment. For a production storage target, Ceph RADOS Gateway is a mature alternative but requires a real multi-host Ceph deployment; a single-host container stack does not establish production durability. ZFS can back local storage directories, but it is not an object-store API and does not by itself meet DuckDB's catalog-backed Iceberg write path.

## Experiments

1. Create a deterministic table with one DuckDB writer; confirm every ClickHouse node reads matching counts and aggregates.
2. Run N writers with disjoint append batches while ClickHouse issues reads. Record commits, retries, snapshot IDs, latency, and exactly-once row checks.
3. Increase N and observe contention, file growth, and orphan files. Test overlapping updates separately from disjoint appends.
4. Verify snapshot visibility and row-level delete behavior across DuckDB and ClickHouse, including Iceberg v2 positional deletes and v3 deletion vectors where supported.
5. Test catalog restart and storage credential failures independently.
6. Add another Iceberg implementation (for example Spark or PyIceberg) only after the two-engine baseline works.

## Current compatibility caveat

DuckDB documents Iceberg `INSERT`, `UPDATE`, `DELETE`, and `MERGE` when attached to a REST catalog. ClickHouse's current Iceberg support matrix lists `INSERT` as Beta, `DELETE` as experimental, and `UPDATE`/`MERGE` as unsupported. The benchmark's separate capability results should therefore inform which concurrency tests are meaningful here.

## References

- [DuckDB Iceberg writes](https://duckdb.org/docs/current/core_extensions/iceberg/writing)
- [ClickHouse Iceberg support matrix](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/use-cases/data_lake/support-matrix.md)
- [SeaweedFS production setup](https://github.com/seaweedfs/seaweedfs/wiki/production-setup)
- [Ceph RADOS Gateway](https://docs.ceph.com/en/latest/radosgw/)
- [Iceberg concurrent-write reliability](https://iceberg.apache.org/docs/latest/reliability/)
