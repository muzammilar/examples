# Appending while a native rewrite transaction is open

`make duckdb-conflicts` passes on **DuckDB 2.0.1 / Iceberg f979b5b28a**,
SeaweedFS **4.54** and ClickHouse **26.10.10.3**. Start the current-profile
infrastructure with this example's README lifecycle first. All **218 host
controls** pass. This is a focused transaction-conflict test, not a capacity or
continuous recovery benchmark.

1. Commit **100,000 rows** in ten 10,000-row batches, across 10,000 customers.
2. Begin a native rewrite transaction and stage **10 → 1 files**. Keep it open.
3. In another process, commit **10,000 new rows**. A third process verifies every
   body and the new published snapshot while the rewrite is still uncommitted.
4. Attempt the stale rewrite's `COMMIT`. It returns **`Conflict_409`**, explaining
   that branch `main` changed from the planned snapshot to the append's snapshot.
5. Verify committed catalog metadata is identical to the append's metadata and
   all **110,000 acknowledged rows** remain correct.
6. Replan in a **new transaction**. The native rewrite replaces **11 → 1 files**
   and publishes a new snapshot without losing the append. ClickHouse independently
   validates every ID, customer, amount and payload hash.

The checker binds the native conflict message to both actual snapshot IDs, checks
all eleven acknowledged intervals and rejects a successful stale commit in this
specific rejection scenario. A generic connection failure cannot establish a
commit conflict. Each transaction uses a separate native CLI process, with a
bounded input gate holding the first transaction open. No application requests
are retried silently.

The recovery procedure observed here is to end the rejected transaction and
**plan a fresh rewrite against current metadata**. Replaying only the old
`COMMIT` is not the tested procedure. HTTP retries and Iceberg commit-conflict
replanning are different mechanisms. The pinned source disables ordinary append
retry when a transaction has explicit requirements; see
[`SupportsAppendRetry`](https://github.com/duckdb/duckdb-iceberg/blob/f979b5b28a044c49c38a26636ac01bc1f7047fa5/src/catalog/rest/transaction/iceberg_transaction_data.cpp#L192).

The final run `e47c037f-5ba8-4180-9185-526389d16afb` records:

| Role | Observed CPU, core-s | Mean CPU, one core = 100% | Cgroup lifetime peak, MiB |
|---|---:|---:|---:|
| Native CLI sequence, including overlapping processes | 2.067 | 37.92% | 267.9 |
| Storage/catalog | 0.859 | 12.90% | 1,029.9 |
| ClickHouse | 1.492 | 22.33% | 1,539.4 |

Both overlapping CLI processes share one **2 CPU / 2 GiB** cgroup; each has two
threads and 1 GB decimal native memory/spill limits. Storage has 1 CPU / 1 GiB;
ClickHouse has 2 CPU / 1.5 GiB. HTTP policy is **30 s timeout, 3 retries, 100 ms
initial wait, backoff factor 4**, dumped in the results. It does not establish a
hard end-to-end deadline. The independent ClickHouse verification took
**217.787 ms**, including Docker exec, connection, SQL, result transfer and JSON
decoding; it is one diagnostic observation, not a latency distribution.

The resulting Parquet file is **8,140,033 bytes** for 110,000 rows, with an
**840-byte footer**. Encoded chunks are **8,866,306 uncompressed / 8,106,379
compressed bytes (1.0937×)**. The schema is BIGINT event ID, INTEGER customer,
BIGINT amount and 64-character VARCHAR SHA256. IDs are 0–109,999; customer is
ID modulo 10,000, amount is ID modulo 10,000 plus one, payload is SHA256 of its
decimal ID. No nulls or RNG; each customer has eleven rows after the append.

[`DUCKDB_CONFLICT_RESULTS.json`](DUCKDB_CONFLICT_RESULTS.json) retains native
file/byte counters, per-role CPU/memory/allocated disk and device read/write
bytes/operations/rates, exact source hashes and ignored raw evidence references.
Sampling is approximately one second, with a final cgroup hold after short-lived
children exit. Role windows differ; counters include probes/background work,
server lifetime peaks may predate the run, and missing CLI block counters mean
unavailable. These are shared-host observations, not reserved independent nodes.
The shared-runner rejection regression also passes; its original evidence is
retained separately, not pooled with this trial.

This fixed diagnostic fixture stays small because retained storage has only two
free native volume slots. The earlier two-million-row healthy rewrite remains
separate evidence. The replan here runs after the bounded appender exits:
**continuous-writer progress/starvation is still untested**. Do not pause writers
to manufacture a production recovery pass. Million-row native compactor process,
S3/catalog interruptions and independent continuous readers/writers remain required.
