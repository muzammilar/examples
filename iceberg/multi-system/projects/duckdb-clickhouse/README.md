# DuckDB → ClickHouse ↔ Iceberg

HTTP application route, preserved native-connector regressions, concurrent access and compactor interactions.

This is a project within the [parent lab](../../README.md). The parent owns
the Docker infrastructure, workload image builds, lifecycle guards and ignored
raw evidence. This directory groups this project's controllers, offline tests,
guides and compact reports; it does not create a second independent cluster.

## Quick start

Run these commands from this directory. They delegate to the parent lab's
lifecycle and preserve its ownership and failed-evidence guards. Experiments
are currently paused; these commands document the existing workflows.

1. `make docker-up` — prepare the parent lab infrastructure.
2. `make up` — build/configure the lab workload.
3. `make test` — run this project's selected scenario.
4. `make down` — stop/remove the workload as documented by the parent.
5. `make docker-down` — remove the parent lab's owned infrastructure.

## Why we replaced `clickhouse_scanner`

Our verdict on the tested revision: **horrible results for this workload**. We
rejected it as the default connector because successful queries could return
wrong answers. It remains in the repository as an explicitly named regression
path, `make duckdb-clickhouse-native`, rather than having its failures erased.

With DuckDB **2.0.1**, signed `clickhouse_scanner` **ce337c48c1** and ClickHouse
**26.10.10.3**, the same generated **2,000,000-row** dataset produced:

| Check | One thread | Two threads |
|---|---:|---:|
| Streaming query's reported invalid-body count | 1,189,514 | 787,154 |
| Actual invalid bodies after complete local materialization | 0 | 0 |
| Incorrect `ingress_bad=true` flags saved beside correct values | 0 | 1,356,530 |

The CLI exited successfully despite those wrong results; our verifier failed the
run. The reproducer does not use Iceberg or object storage. Independent native
ClickHouse checks found correct stored bodies, and DuckDB-only generated checks
passed. This implicates the DuckDB/connector execution path, but we have **not
isolated whether the defect belongs to DuckDB or the extension**. Materialization
changes the plan and is not a qualified workaround.

See the [row-level isolation](clickhouse-scanner/docs/CONNECTOR_READ_ISOLATION.md),
[compact measured results](clickhouse-scanner/reports/CONNECTOR_READ_ISOLATION_RESULTS.json) and
[connector findings](clickhouse-scanner/docs/DUCKDB_CLICKHOUSE_FINDINGS.md). Other limitations include
catalog discovery failures, remote writes surviving `ROLLBACK`, and the documented
loss of sub-microsecond timestamp precision. Those documented semantics are
separate from the unexpected wrong-answer failure.

The default now uses [core `httpfs` reads and signed `http_client` writes](http/docs/DUCKDB_HTTP.md).
A separate healthy run verified **2,000,000 rows each in hot, cold and restored
state**, with zero invalid bodies and independent ClickHouse readback; see its
[results](http/reports/DUCKDB_HTTP_FULL_RESULTS.json). This establishes the measured
healthy round trip, not general production reliability or HTTP fault recovery.

## Separate implementation directories

| Implementation | Directory | Status |
|---|---|---|
| Native `clickhouse_scanner` | [clickhouse-scanner/](clickhouse-scanner/README.md) | Rejected default; failed correctness results and reproducers retained |
| Core `httpfs` + signed `http_client` | [http/](http/README.md) | Default; healthy round trip passed, production qualification open |

Each implementation has its own Makefile, scripts, documentation and reports.
`tests/test_duckdb_clickhouse.py` is the shared regression suite for both paths.
The parent Makefile selects HTTP by default; the native directory explicitly
selects the scanner. Raw histories stay at their original lab-relative paths.
