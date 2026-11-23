# Row-level isolation of the connector wrong answer

2026-11-23: **failed** on DuckDB 2.0.1 with signed community
`clickhouse_scanner` ce337c48c1 and ClickHouse 26.10.10.3. Executed:

```sh
make connector-read-isolation ROWS=2000000
```

This reads deterministic generated ClickHouse rows, without Iceberg or object
storage. It compares a DuckDB-only generator, streaming validation and a temporary
DuckDB table containing every received value plus the predicate's Boolean result
at ingestion. It recomputes the predicate on all materialized rows afterward.

| Observation | One thread | Two threads |
|---|---:|---:|
| DuckDB-only generated invalid bodies | 0 | 0 |
| Connector streaming invalid-body count | **1,189,514** | **787,154** |
| Invalid bodies after complete local materialization | 0 | 0 |
| Saved `ingress_bad=true` flags | 0 | **1,356,530** |

Every full check sees exactly 2,000,000 unique IDs, bounds 0–1,999,999 and sum
10,001,000,000 cents. No null flags were recorded. Twenty saved two-thread examples
contain correct original values with `ingress_bad=true`, yet `recomputed_bad=false`
on the stored row. Independent host Python integer/SHA256 checks confirm all
20 examples; that check is a sample, not a separate full-data export.

The error is **not confined to the aggregate counter**: an incorrect predicate
result can be stored alongside correct values. The culprit between DuckDB and
its connector remains unassigned. Each query performs a separate scan, and
materialization changes its execution plan. Do not call it a reliable workaround
or stored-data corruption. The upstream issue draft remains unposted.

The CLI returns exit 0; the host correctly fails Make with
`stream-1, stream-2, flags-2`. The live workflow passed 247 controller controls.
A later negative control also rejects wrong saved flags when other summaries pass;
it does not rerun or reclassify this trial. The
[compact result](CONNECTOR_READ_ISOLATION_RESULTS.json) pins 33 raw artifacts,
versions, source/image hashes and the independent sample check.

Inspected the pinned connector's
[scan](https://github.com/altertable-ai/duckdb-clickhouse/blob/ce337c48c12d24630caeed151b79dc177d48eb86/src/clickhouse_scanner.cpp),
[conversion](https://github.com/altertable-ai/duckdb-clickhouse/blob/ce337c48c12d24630caeed151b79dc177d48eb86/src/clickhouse_conversion.cpp)
and [connection](https://github.com/altertable-ai/duckdb-clickhouse/blob/ce337c48c12d24630caeed151b79dc177d48eb86/src/clickhouse_connection.cpp)
paths. Workers convert native blocks into DuckDB vectors; strings are copied using
`StringVector::AddString`. This does not establish a buffer-lifetime, validity-mask
or thread-safety defect. Six inspected source hashes are saved; downloads stay
ignored.

## Resource observations and limits

Four input fields represent **168,000,000 logical bytes per two-million-row
scan**, excluding framing and the diagnostic flag. Data is generated; there is
no new persisted dataset or compression layer. Temporary tables are dropped.
No cloud resources or host AWS configuration are used.

| Role | CPU core-seconds | Mean CPU (one core = 100%) | Sampled charged memory peak MiB |
|---|---:|---:|---:|
| DuckDB CLI | 6.575 | 66.08% | 314.49 |
| ClickHouse | 6.114 | 53.59% | 1,457.45 |
| SeaweedFS/instrumentation | 0.172 | 1.51% | 804.68 |

Whole-case windows include setup, generation, materialization, checks and probes;
they differ by role and are not per-query costs. CLI caps are 2 CPU / 2 GiB and
1 GB engine memory; ClickHouse retains 2 CPU / 1.5 GiB and 1 GiB server memory.

Allocated ClickHouse bytes change **3,491,479,552 → 3,504,799,744**; the CLI results
bind changes **59,834,368 → 59,949,056**. Storage stays **3,651,428,352**. Background
logs/probes can change these independently of data. ClickHouse VM device counters
record zero read bytes, **24,002,560 write bytes / 4,954 writes** over 11.41 s
(2,103,642 write bytes/s, 434.18 write IOPS). Storage counters are zero; CLI device
counters are unavailable. Physical disk latency, query-only attribution and
sustained capacity remain unmeasured. Raw samples preserve cache/counter scope,
lifetime peaks and gaps.
