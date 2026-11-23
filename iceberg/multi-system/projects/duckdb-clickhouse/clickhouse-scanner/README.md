# Native clickhouse_scanner: failed qualification

The tested scanner produced horrible results for this workload: successful queries
returned wrong predicate results. We rejected it as the default, and preserve its
source, reproducers, limitations and failed verdicts here.

This route belongs to the [DuckDB–ClickHouse project](../README.md) within the
[multi-system lab](../../../README.md). The enclosing lab owns its infrastructure,
builds and original ignored raw evidence. Experiments remain stopped.

## Quick start

Run these separately from this directory when experiments are authorized:

1. `make docker-up` — prepare the route's lab infrastructure.
2. `make up` — build/configure the workload.
3. `make test` — run this route's round-trip checks.
4. `make down` — remove the workload with preservation guards.
5. `make docker-down` — remove only owned infrastructure and state.

The native `test`, `read-repro` and `read-isolation` entry points preserve the
known regressions and return nonzero when the mismatches reproduce. They are
not expected green qualification checks. No failed verdict was removed.

Read the [findings](docs/DUCKDB_CLICKHOUSE_FINDINGS.md),
[row-level isolation](docs/CONNECTOR_READ_ISOLATION.md) and
[measured results](reports/CONNECTOR_READ_ISOLATION_RESULTS.json).

## Files

- `scripts/`: this route's controllers and report helpers.
- `docs/`: its findings, procedures and limitations.
- `reports/`: its compact, tracked results.
- [Shared regression tests](../tests/test_duckdb_clickhouse.py) cover both routes.
- Raw run directories remain unchanged under the enclosing lab's ignored `results/`.
