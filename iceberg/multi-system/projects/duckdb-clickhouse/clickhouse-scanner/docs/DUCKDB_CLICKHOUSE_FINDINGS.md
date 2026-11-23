# DuckDB as the ClickHouse client, with Iceberg behind ClickHouse

The requested path is **application reads/writes through DuckDB → ClickHouse ↔
Iceberg**. These preserved probes used DuckDB's `clickhouse_scanner` extension
as the application connector; DuckDB did not access S3 directly in these probes. This differs from the existing
direct DuckDB–Iceberg examples. **The native scanner failed qualification and
was replaced as the default.** See the [project verdict and measured wrong-answer
counts](../../README.md#why-we-replaced-clickhouse_scanner) and the
[HTTP replacement](../../http/docs/DUCKDB_HTTP.md).

Local diagnostic probes used DuckDB **2.0.1**, signed community connector
**ce337c48c1**, ClickHouse **26.10.10.3** and SeaweedFS **4.54**. No host cloud
credentials or real AWS resources were used. SQL ran in the existing C++ DuckDB
CLI image, with Python only controlling Docker and collecting evidence.

## Confirmed observations

The later [two-million-row round trip](DUCKDB_CLICKHOUSE_ROUNDTRIP.md) also found
intermittent wrong answers in the DuckDB/connector read-query path. A generated-
row regression reproduces it without object storage; both one and two threads
have failed across trials. Independent native full-body checks find the stored
hot, cold and restored data correct. This remains a failed qualification.

| Boundary | Measured result | Classification |
|---|---|---|
| Iceberg catalog discovery | Ordinary `ch.lake."lab.probe"` lookup fails; explicit `clickhouse_scan` also reports table not found. A native query through `clickhouse_query` reads the same 10,000-row table, sum 50,005,000. | Integration failure; architecture remains unqualified |
| Rollback | Insert ID 2 inside `BEGIN`, run `ROLLBACK`, then read IDs 1 and 2 successfully. | Documented absence of remote rollback; not an atomic application transaction |
| Nanosecond timestamps | ClickHouse stores `1767225600123456789` ns; ordinary connector reads return `1767225600123456000` ns, losing **789 ns**. Native pass-through integer nanoseconds preserve the value. | Documented microsecond mapping; material when exact nanoseconds matter |
| Community installation | Official `INSTALL clickhouse_scanner FROM community` and `LOAD … FROM community` work with signature checks enabled. Direct file-path loading fails. | Initial probe used the wrong loading method; not an invalid upstream signature |

For discovery, `system.tables WHERE database='lake'` returns no rows even though
the native query reads the catalog-managed table. The pinned
[`ClickhouseTableSet::LoadEntries`](https://github.com/altertable-ai/duckdb-clickhouse/blob/ce337c48c12d24630caeed151b79dc177d48eb86/src/storage/clickhouse_table_set.cpp)
builds table entries from `system.tables` and `system.columns`. This is consistent
with the discovery gap; it does not establish whether the fix belongs in
ClickHouse metadata enumeration, the connector, or both. Cache-clear recovery
has not been established. Pass-through is a diagnostic control, not evidence that
ordinary attached-table access works.

The [connector documentation](https://github.com/altertable-ai/duckdb-clickhouse)
explicitly states that writes commit independently, rollback cannot undo them,
failed inserts may leave partial data, and DateTime64 reads have microsecond
precision. The pinned transaction and insert implementations were inspected.
Partial/ambiguous writes therefore need reconciliation rather than an assumption
that DuckDB transaction syntax supplies remote atomicity.

DuckDB 2.0's
[`extension_load.cpp`](https://github.com/duckdb/duckdb/blob/v2.0.1/src/main/extension/extension_load.cpp)
uses installation-origin metadata to choose trusted signing keys; direct paths
without that metadata use core keys. The successful official installation
reports connector revision `ce337c48c1`, matching its binary footer and reviewed
source. No unsigned-extension flag was used.

The earlier [ClickHouse-written manifest issue](../../../manifest-interop/docs/CLICKHOUSE_MANIFEST_ISSUE.md)
also remains relevant to independent readers: ClickHouse omits required `content`
metadata. Reading that output through ClickHouse alone does not prove Iceberg
interoperability with other engines.

## Evidence and scope

[`DUCKDB_CLICKHOUSE_FINDINGS.json`](../reports/DUCKDB_CLICKHOUSE_FINDINGS.json) binds 32 raw
evidence files to hashes, records settings and per-role CPU, memory, allocated
disk and device read/write bytes/I/O, and retains failed verdicts. Scanner binary
SHA256: `e32c23138bc7eb620e428bbc6f9b62d7984cdc9d7e94cc19299ae18d852cd5ab`.
Raw source, SQL, installation artifacts and logs remain ignored under
`results/current/duckdb-clickhouse-*`.

The rollback fixture contains two rows and the timestamp fixture one. The
10,000-row pass-through control checks count and sum, **not complete bodies**.
Two initial nested-SQL quoting failures remain harness failures. Later probes
fail at actual catalog/scanner lookup, with native child exit 1. Failure cleanup
fences the holding shell; its termination is not a DuckDB native abort. Resource
windows include that hold/fencing interval. These probes provide no request
latency distribution, compression result or capacity ranking.

`make duckdb-clickhouse-native` preserves the two-million-row native round trip and fails
closed before advancing past a wrong body summary. The independent
`make duckdb-clickhouse-read-repro` also returns nonzero on the observed generated
read-query mismatch. Both run within this example's existing infrastructure
lifecycle; their execution does not qualify the architecture. Group 1 has been
exercised and failed; the preserved five-group scope is documented below.
Experiments are stopped; this list does not authorize further runs:


1. **Full round trip:** two million rows through DuckDB → ClickHouse → Iceberg →
   ClickHouse, complete body/type oracle and ordinary versus pass-through queries.
   Resolve or clearly mark the discovery failure.
2. **Insert failure/retry:** [late conversion failure and blind-retry duplicate
   effects](DUCKDB_PARTIAL.md) reproduced at two million input rows; corrected
   immutable-key replay passes in a separate replacement table with FINAL reads.
   Transport-level ambiguous acknowledgements and live durable-witness faults
   remain unrun.
3. **ClickHouse restart:** [live retained-volume SIGKILL](DUCKDB_CRASH.md) passes
   with 2.14M ACKed rows and independent ordinary DuckDB reads/writes. Fresh-process
   reconnect only; persistent pools, physical power failure and earlier-history
   ownership closure remain unqualified.
4. **S3/catalog outage:** [endpoint-specific partitions](DUCKDB_DEPENDENCIES.md)
   pass with 3.38M hot ACKs, a read-only 2M cold source and separate 3k write probe.
   Both ambiguous 1k archive requests applied after healing, including after native
   client fencing; no blind retries. Full service process outages and bulk-archive
   capacity are separate unrun variants.
5. **Concurrent maintenance:** [full two-million-row rewrite](CONNECTOR_CONCURRENT.md)
   executed with live hot/cold writers/readers and ten cold ACK intervals inside
   the native CALL. All 3.46M hot / 2.097M cold ACK bodies reconcile. Availability
   fails: 32/51 cold reads hit memory limits. Snapshot expiration and safe cleanup
   remain unrun; no capacity qualification or automatic performance expansion.

Apply `insert-batch-size` and `decision-ingestion-strategy`: use equivalent bounded
10k–100k-row batches before capacity comparisons. Per `agent-query-safety`, keep
explicit execution, memory, scan and output caps, with throwing overflow behavior
so partial output cannot pass correctness checks. Broader operator, topology and
comparative-performance coverage is not completed by these diagnostic probes.
