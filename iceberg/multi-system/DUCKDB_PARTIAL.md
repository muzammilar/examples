# Failed inserts can commit data, and retries can duplicate it

`make duckdb-clickhouse-partial ROWS=2000000` passed its failure/recovery assertions
on 2026-11-22 using native C++ DuckDB **2.0.1**, signed community connector
**ce337c48c1**, and local ClickHouse **26.10.10.3**. All **231** host controls pass.
This is part of group 2; transport-level ambiguous acknowledgements remain untested.
Use this example's five-command infrastructure lifecycle before running the target.

The application inserts two million `(event_id Int64, value Int8)` rows through
DuckDB's ordinary attached ClickHouse table. IDs are `0..1999999`; every valid
value is `1`, but the final input value is deliberately `128`, outside Int8.
Connector insert blocks are 100,000 rows; two DuckDB threads are enabled. These
are two-column deterministic inputs, **18,000,000 logical field bytes** per valid
replay, not the earlier four-column payload/customer fixture or a capacity test.

| Observation | Stored rows | Unique IDs | Wrong values |
|---|---:|---:|---:|
| First statement returned conversion error | 1,906,688 | 1,906,688 | 0 |
| Same invalid statement retried; same error | 3,813,376 | 1,906,688 | 0 |
| Subsequent valid one-row write acknowledged | 3,813,377 | 1,906,689 | 0 |
| Corrected input replayed twice into separate replacement table, `FINAL` | 2,000,000 | 2,000,000 | 0 |

The failed statements acknowledge **zero rows**. Persisted effects do not convert
errors into acknowledgements. The observed prefix size is version/execution
specific, not a promised atomicity or block boundary. Both initial diagnostic runs
reproduced the same partial effects. No upstream bug is alleged: the connector
[documents](https://github.com/altertable-ai/duckdb-clickhouse/blob/ce337c48c12d24630caeed151b79dc177d48eb86/README.md)
that failed inserts may persist data and remote writes cannot be rolled back.

Recovery preserves the original table, copies its observed effects into a new
`ReplacingMergeTree ORDER BY event_id`, and inserts the **corrected** two-million-row
input twice through DuckDB. Native ClickHouse `FINAL` verifies every ID and value:
exact distinct/count, bounded integer IDs and no incorrect values. This works for
immutable event bodies; conflicting updates require a separately specified
version/conflict policy. Replacement merges are asynchronous and ordinary reads
can expose duplicates. This is neither transactional rollback nor exactly-once
insertion. The separate wrong-answer finding in ordinary connector read queries
remains unresolved; one-row native aggregate pass-through observations here do
not qualify that read path.

The host fsyncs deterministic intent declarations before release and checks both
native conversion errors, valid-write ACKs and independent native effects. ACK
files are fsynced during final reconciliation. No fault is injected into the
witness here; this does not establish power-loss-safe live journaling. The CLI
continues after the two expected errors and retains its actual **exit 1**; Make
passes only after those specific errors and all reconciliation assertions pass.
Failed native state and all raw diagnostics remain preserved.

The [compact result](DUCKDB_PARTIAL_RESULTS.json) retains hashes for SQL, source
invocation, native exits/errors, ACKs, profiles, storage and resource samples.
Corrected two-million-row INSERTs took **96.513 ms** and **93.502 ms** at the native
query boundary (two samples, no latency percentile or throughput ranking).
ClickHouse used **1.998 core-seconds**, **31.17%** of one core over its sampled
6.41-second interval; charged memory peaked at **1,501.80 MiB**. DuckDB used
**0.416 core-seconds**, **8.05%** over 5.16 seconds, with **70.73 MiB** lifetime
cgroup peak. Role windows include probes/holding time and differ.

Original parts: **34,320,393 uncompressed / 15,294,599 compressed bytes**;
**15,313,481 bytes** on disk. Replacement parts at observation:
**36,000,000 / 16,040,418 bytes**, **16,054,686 bytes** on disk, containing
4,000,000 physical rows despite 2,000,000 logical `FINAL` rows. Background merges
can change physical counts. Per-role allocated-disk samples and per-device
read/write bytes, operations and rates are retained. The short ClickHouse window
observed only 16,384 device write bytes / 4 writes and no device reads: dirty-page
writeback can occur later, so these are **not complete physical I/O costs**.
DuckDB device counters were unavailable. Shared-host caches were not cleared;
server lifetime memory peaks may predate the experiment. No S3 data was written.

This follows `schema-types-native-types`, `insert-batch-size` and
`agent-query-safety`: bounded native types, configured 100k transport blocks,
explicit scan/time/memory caps and complete scoped reconciliation. The deliberately
invalid large statement probes partial failure; it is not an ingestion recommendation.
