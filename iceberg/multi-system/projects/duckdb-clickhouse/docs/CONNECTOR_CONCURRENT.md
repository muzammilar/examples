# Concurrent Iceberg rewrite through the DuckDB/ClickHouse application path

2026-11-23: **integrity passed; availability failed**. This is a bounded
maintenance diagnostic, not a capacity benchmark or production qualification.
The compact [result](../reports/CONNECTOR_CONCURRENT_RESULTS.json) records versions, resource
samples, per-phase request latencies and SHA256 hashes of 42 raw evidence files.

## Execution and result

The executed command was:

```sh
BASE_CASE=7cdc061e-42ff-43e4-b0d9-4cca84a9d286 make connector-concurrent ROWS=2000000
```

The witnessed parent has advanced; do not blindly reuse its receipt or reset its
data. A new run requires a fresh passed baseline. Application SQL uses the native
DuckDB 2.0.1 CLI and clickhouse_scanner ce337c48c1 against ClickHouse 26.10.10.3.
Spark 4.1.4 / Iceberg 1.12.0 performs maintenance on local SeaweedFS 4.54 through
the same REST catalog. No cloud resources or host AWS credentials are used.

Independent hot/cold readers and writers stay live. Cold reads use explicit native
query pass-through because catalog discovery remains broken. The four-column
fixture contains ID, customer, cents and a 64-byte SHA256 hex body; two million
records represent 168,000,000 logical bytes before framing. Successful operations
are reconciled against external witnesses by ID and complete expected body.

| Observation | Measured result |
|---|---:|
| Hot seed / subsequent acknowledged rows | 2,000,000 / 1,460,000 |
| Cold inherited / new acknowledged rows | 2,000,000 / 97,000 |
| Missing, wrong or duplicate acknowledged rows | 0 |
| Hot write / read successful requests | 146/146 / 144/144 |
| Cold write successful requests | 97/97 |
| Cold read successful requests | **19/51 (37.25%)** |
| Required first-attempt success rate per role | 95% |
| Native rewrite CALL wall time | 7.960 s |
| Whole acknowledged cold request intervals inside CALL | 10 |
| Native rewritten / added files | 70 / 2 |
| Native rewritten bytes / failed files | 150,404,436 / 0 |

All 32 cold-read failures report ClickHouse `MEMORY_LIMIT_EXCEEDED` under the
unchanged 1 GiB server limit. There were 29 before the broad maintenance-call
phase, one during it and two afterward; this does not establish that compaction
caused every failure. Successful body proofs are correct. Errors remain in the
availability denominator rather than disappearing from successful-read latency.

The original live finalizer checked progress and integrity but missed this rate
gate, so its raw `result.json` says `passed:true`. It is preserved unchanged.
The strict read-only recheck below correctly exits nonzero and makes the overall
compact verdict **false**. Its full hot/cold data verification succeeds.

```sh
make connector-concurrent-recheck CASE=fe20a081-ab77-4c71-9ecb-c2332dce4a7e
```

The final saved recheck is `rechecks/0b9c33c1-2a7b-4d5a-afac-7f2ff0271d2c`.
There are 246 passing host controls. Journal completion, late actor-error checks
and the availability gate were hardened after the live trial, then verified by
negative controls and read-only reconciliation. The native Spark job and actual
application SQL are unchanged; no second full rewrite ran after that hardening.
The original live case predates the explicit actor-error report, so its absence
is recorded rather than replaced by an invented empty report.

## Snapshot and compression boundaries

The pinned snapshots contain 2,069,000 rows / 70 files / 150,404,436 bytes before
and 2,081,000 rows / 14 files / 86,724,744 bytes afterward. The latter includes
two rewrite outputs and twelve concurrent tail files. The old two-million-row
snapshot remains fully readable and correct; its original base file is absent
from current live membership. Spark logged one commit-conflict retry and
successfully rebased without pausing application writers.

Footer compressed/uncompressed bytes are 149,652,936 / 165,707,495 before and
86,571,836 / 165,872,904 afterward. The original SNAPPY base and ZSTD tail become
GZIP rewrite outputs and ZSTD tail. Codec changes and concurrent appends confound
the size difference: this is not an isolated packing or compression-speed win.
No snapshot expiration, orphan cleanup or vacuum was performed.

## Resource observations

Docker Desktop exposes 11 CPUs and 26,216,296,448 bytes RAM on one shared host.
The CLI and Spark each have 2 CPU / 2 GiB caps; ClickHouse has 2 CPU / 1.5 GiB
container caps plus its 1 GiB server limit; SeaweedFS has 1 CPU / 1 GiB caps.
These full-case windows include setup, verification and instrumentation and differ
by role. They are not costs of only the 7.960-second native CALL.

| Role | CPU core-seconds | Mean CPU (one core = 100%) | Cgroup lifetime peak MiB |
|---|---:|---:|---:|
| ClickHouse | 53.257 | 62.71% | 1,536.67 |
| DuckDB CLI workload | 94.853 | 117.03% | 146.84 |
| Spark | 31.869 | 110.54% | 1,705.60 |
| SeaweedFS | 32.017 | 37.71% | 1,029.86 |

Lifetime peaks may predate the observation window. Charged memory includes file
cache/kernel memory and is distinct from process RSS. Allocated ClickHouse bytes
grow from 2,879,213,568 to 3,628,728,320; storage grows from 3,541,712,896 to
3,650,441,216. CLI/Spark share a results bind: do not sum its disk observations.
Spark scratch allocated peak is unavailable separately.

| Role | Device read / write bytes | Read / write operations |
|---|---:|---:|
| ClickHouse | 7,987,200 / 798,736,384 | 339 / 22,086 |
| Spark | 0 / 184,524,800 | 0 / 513 |
| SeaweedFS | 291,377,152 / 108,912,640 | 934 / 260 |

These are Docker-VM device counters, not physical SSD latency. CLI block counters
are unavailable. Sampling cadence, native timing brackets and per-role byte/IOPS
rates are retained in the compact JSON and ignored raw results. No shared-host
caches were cleared. Request counts are too small for strong tail-latency claims.

An earlier single-request two-million-row hot seed failed with the same memory
error before maintenance started; its separate table had zero acknowledged and
zero observed rows. That failed case remains preserved. The subsequent run used
20 bounded 100,000-row seed requests at the same caps, without resetting it.

Ordinary DuckDB query wrong answers and missing v2 manifest metadata remain
separate failed gates. Retention safety, targeted transport ACK loss and broader
production qualification remain open. Before any new performance effort, ask for
a depth from 1–5 (usual preference 3); do not automatically extend this diagnostic.
