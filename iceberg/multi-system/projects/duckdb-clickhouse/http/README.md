# HTTP replacement: healthy round trip passed

The default uses core DuckDB `httpfs` reads and signed `http_client` writes.
The measured healthy hot/cold/restored round trip passed; fault/recovery and
production qualification remain separate, uncompleted work.

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

Read the [interface and limitations](docs/DUCKDB_HTTP.md) and
[full healthy-run results](reports/DUCKDB_HTTP_FULL_RESULTS.json).

## Files

- `scripts/`: this route's controllers and report helpers.
- `docs/`: its findings, procedures and limitations.
- `reports/`: its compact, tracked results.
- [Shared regression tests](../tests/test_duckdb_clickhouse.py) cover both routes.
- Raw run directories remain unchanged under the enclosing lab's ignored `results/`.
