# Two-million-row DuckDB → ClickHouse ↔ Iceberg evaluation

**Failed correctness qualification.** The application uses native DuckDB 2.0.1
with signed `clickhouse_scanner` **ce337c48c1**. It sends reads/writes to ClickHouse
**26.10.10.3**; only ClickHouse accesses Iceberg on local SeaweedFS **4.54**.
No managed Tigris or real AWS resources were used.

After the README's infrastructure setup, run:

```sh
make duckdb-clickhouse-native ROWS=2000000 CUSTOMERS=10000 BATCH_ROWS=100000
make duckdb-clickhouse-read-repro ROWS=2000000
```

The original round-trip target was named `duckdb-clickhouse` when executed;
it is now preserved as `duckdb-clickhouse-native` after the HTTP switch.
Both original Make workflows were run and returned nonzero. **228 host controls pass**;
that does not make the live connector tests pass. The first command inserts
through ordinary DuckDB attached ClickHouse tables, publishes to Iceberg through
`clickhouse_execute`, reads cold rows through `clickhouse_query`, then restores
them into another ClickHouse MergeTree table. The known catalog-discovery failure
prevents ordinary attached cold-table lookup; pass-through is explicitly separate.

## What failed

The first round trip acknowledged **20 × 100,000-row batches** and completed both
transfers. Counts, unique IDs, bounds and amount totals were correct. DuckDB's
complete-body queries nevertheless reported:

| Read path | Rows / unique IDs | Reported bad bodies | Independent native bad/null bodies |
|---|---:|---:|---:|
| Hot ClickHouse table | 2,000,000 / 2,000,000 | 2,000,000 | 0 / 0 |
| Iceberg through ClickHouse | 2,000,000 / 2,000,000 | 1,471,954 | 0 / 0 |
| Restored ClickHouse table | 2,000,000 / 2,000,000 | 1,286,487 | 0 / 0 |

The native checker independently evaluated every ID/customer/amount/hash, nulls,
unique IDs and ID bounds on all three copies. It found no stored corruption or
acknowledged loss. Read-only DuckDB diagnostics also returned correct sampled
values and correct column-specific checks; a locally materialized copy passed
the original combined predicate. These controls do not pass the original run.

The wrong answer also reproduces over **generated ClickHouse rows**, with no
persisted table or object-storage dependency. The packaged Make reproducer reads
the same generated dataset with one and two DuckDB threads. Its final trial
reported **873,352 bad bodies with one thread and zero with two**. An earlier
diagnostic reported zero with one and **1,018,366** with two. This is intermittent:
**single-thread mode is not a validated workaround**. Disabling statistics
propagation also did not resolve the observation.

The issue is in the DuckDB/connector read-query path; the precise root cause
between engine and extension is not established. See the unposted
[`DUCKDB_CLICKHOUSE_READ_ISSUE.md`](DUCKDB_CLICKHOUSE_READ_ISSUE.md) reproducer.
Do not label the erroneous counters as stored data loss, or infer correctness
from another query shape returning the expected answer.

The final round-trip target now checks each saved body summary **before** the
next transfer. Its separate two-million-row rerun failed at the hot-table gate,
retained all acknowledged source rows and left Iceberg with **no snapshot**.
Independent native verification proves all two million hot bodies remain correct.
The initial exploratory run performed transfers before its host-side body check;
that original failed result remains preserved. Native positive/negative controls
verify that the new gate accepts a correct summary and rejects a wrong body or
duplicate summary.

## Measured costs and storage

This is **one failed functional trial, not a capacity benchmark**. The first
workflow took **23.225 s**, including 227 controls, first-time connector preparation,
startup, transfers, checks and fencing. Native DuckDB profiles record:

| Operation | Observed time |
|---|---:|
| Twenty 100k-row inserts | 2,535.558 ms total; 104.849–262.248 ms per batch |
| Hot full-body query + JSON COPY | 611.465 ms |
| ClickHouse → Iceberg publication call | 1,750.335 ms |
| Cold full-body query + JSON COPY | 641.173 ms |
| Iceberg → ClickHouse restore call | 603.983 ms |
| Restored full-body query + JSON COPY | 536.171 ms |

These include query execution/fetch/validation and local JSON output where used.
They are not individual-row latency, p99, or measurements of correct successful
queries. First-time public artifact installation is separate from measured
backend intervals. Do not calculate a throughput winner from these observations.

| Role | Observed CPU, core-s | Mean CPU, one core = 100% | Lifetime cgroup peak, MiB |
|---|---:|---:|---:|
| DuckDB CLI | 3.893 | 33.88% | 187.8 |
| ClickHouse | 10.883 | 81.52% | 1,539.4 |
| Storage/catalog | 1.065 | 7.98% | 1,029.9 |

Caps were 2 CPU / 2 GiB for the application, 2 CPU / 1.5 GiB for ClickHouse and
1 CPU / 1 GiB for storage. Role intervals differ; CPU includes probes, charged
memory includes file cache, and server lifetime peaks can predate this run.
These are shared Docker-VM observations, not reserved independent machines.
Per-device read/write bytes/operations/rates and allocated disk are retained in
[`DUCKDB_CLICKHOUSE_ROUNDTRIP_RESULTS.json`](DUCKDB_CLICKHOUSE_ROUNDTRIP_RESULTS.json).
Unavailable counters remain unavailable, not zero.

The four columns are BIGINT ID, INTEGER customer, BIGINT amount and a 64-character
VARCHAR SHA256. IDs are 0–1,999,999; customer is ID modulo 10,000, amount is ID
modulo 10,000 plus one, and payload is SHA256 of decimal ID. No nulls or RNG.
Hot MergeTree parts contain **184,000,000 uncompressed / 137,177,513 compressed
bytes (1.3413×)**; restored parts contain **184,000,000 / 136,873,420 bytes
(1.3443×)**. Allocated part bytes are 137,250,890 and 136,905,412 respectively.
Iceberg published **two data files totaling 81,482,587 bytes**; its encoded Parquet
chunk compression ratio was not measured. These are different physical layouts.

## Reproducibility and remaining scope

The C++ CLI/shared library is built by `app/Dockerfile.duckdb2`. The example's
existing Compose `runner` executes the application SQL. A separate, bounded,
credential-free local preparation container installs the official community
extension before the application joins the internal-only lab network. Its binary
hash/revision and enabled signature checks are verified. Cached extension files
and install-origin metadata are materialized as regular hard links/copies inside
each ignored result directory; no host-installed database or credentials are used.
Python reuses the established Docker/evidence controller rather than replacing
the native application with another implementation.

Raw acknowledgements, original failed verdicts, exact source copies, profiles and
diagnostics stay ignored under `results/current/`. Recorded SQL matches the final
generated round-trip and read-regression SQL. A later scope-text-only correction
clarifies that the generated regression has no independent engine export.
No failed fixture was reset. Existing guarded teardown remains blocked.

Test group 1 is implemented but failed. Its healthy read contract must be fixed
before capacity qualification. Groups 2–5 remain: partial/ambiguous inserts and
retry, ClickHouse restart under live load, S3/catalog outage, then concurrent
maintenance/reconciliation. Failure exploration can continue with the known
incorrect-read condition explicit; it cannot establish production readiness.
