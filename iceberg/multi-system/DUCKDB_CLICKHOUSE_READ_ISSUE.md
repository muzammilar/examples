# Wrong aggregate answers through the ClickHouse connector

Draft upstream reproducer, **not posted externally**. Observed on 2026-11-22
with DuckDB **2.0.1**, signed community `clickhouse_scanner` **ce337c48c1**
(`ce337c48c12d24630caeed151b79dc177d48eb86`), ClickHouse **26.10.10.3**, Linux
ARM64 in isolated Docker. Connector binary SHA256:
`e32c23138bc7eb620e428bbc6f9b62d7984cdc9d7e94cc19299ae18d852cd5ab`.

On 2026-11-23, searched open DuckDB issues mentioning ClickHouse and listed the
connector's open issues through GitHub's API. No matching open report was found;
this is a bounded search, not proof that no related report exists. The connector
tracker is the first reporting destination until a DuckDB-core-only reproducer
isolates the engine. This draft has not been submitted.

From this example, after infrastructure setup:

```sh
make duckdb-clickhouse-read-repro ROWS=2000000
```

The generated source needs no persisted fixture or S3 access:

```sql
SELECT toInt64(number) AS event_id,
       toInt32(number % 10000) AS customer_id,
       toInt64(number % 10000 + 1) AS amount_cents,
       lower(hex(SHA256(toString(number)))) AS payload_sha256
FROM numbers(2000000)
LIMIT 2000000
```

DuckDB runs this through `clickhouse_query('ch', '<query above>')` and applies:

```sql
SELECT count(*) AS rows, count(DISTINCT event_id) AS unique_ids,
       min(event_id) AS first_id, max(event_id) AS last_id,
       sum(amount_cents) AS amount_total_cents,
       count(*) FILTER (WHERE event_id IS NULL OR customer_id IS NULL
         OR amount_cents IS NULL OR payload_sha256 IS NULL
         OR event_id < 0 OR event_id >= 2000000
         OR customer_id != event_id % 10000
         OR amount_cents != event_id % 10000 + 1
         OR payload_sha256 != sha256(CAST(event_id AS VARCHAR))) AS invalid_bodies
FROM clickhouse_query('ch', '<query above>');
```

Expected: 2,000,000 rows/unique IDs, bounds 0–1,999,999, sum 10,001,000,000,
and **zero invalid bodies**. The native query succeeds, but that last count is
intermittently wrong. One packaged run returned **873,352** with `threads=1`
and zero with `threads=2`; an earlier run returned zero and **1,018,366**
respectively. Thread count changes the observation but neither setting is
established as safe. Disabling statistics propagation did not resolve it.

On the persisted two-million-row fixture, native ClickHouse full-body checks of
hot, Iceberg and restored copies all pass. Separate DuckDB column predicates and
sampled values also pass, as does the combined predicate after local
materialization in a diagnostic. These controls narrow the investigation; they
do not fix or reclassify the failed connector query. Root cause between DuckDB
and the extension remains unassigned.

The official community installation succeeds with unsigned extensions disabled.
Both thread settings use the same source, native version, connector binary,
2 CPU / 2 GiB application cap, two server threads and bounded query settings.
The regression saves both answers before exiting nonzero on a mismatch.
Raw SQL, profiles, native exit/state, source and resource observations are bound
in [`DUCKDB_CLICKHOUSE_ROUNDTRIP_RESULTS.json`](DUCKDB_CLICKHOUSE_ROUNDTRIP_RESULTS.json).
See the [full failed round-trip report](DUCKDB_CLICKHOUSE_ROUNDTRIP.md) for dataset,
limits, independent verification and scope.

## Row-level counterexample (2026-11-23)

`make connector-read-isolation ROWS=2000000` also fails: streamed aggregate bad
counts are 1,189,514 (one thread) and 787,154 (two). Two-thread materialization
stores 1,356,530 true bad-body flags beside rows whose full local recomputation
finds zero bad bodies. All 20 saved examples pass an independent Python value/hash
check and have `ingress_bad=true`, `recomputed_bad=false`. This narrows the failure
beyond aggregate counting without assigning a DuckDB-core versus extension cause.
See [row-level evidence](CONNECTOR_READ_ISOLATION.md). This remains unposted.
