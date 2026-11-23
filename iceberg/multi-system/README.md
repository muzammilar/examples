# Iceberg across DuckDB, ClickHouse and object storage

Each data path below has its own guide. This example uses isolated local Docker
Compose services; no cloud credentials or real AWS resources are needed.

| Data path | Guide | Entry point |
|---|---|---|
| DuckDB SQL → ClickHouse ↔ Iceberg | [HTTP application](projects/duckdb-clickhouse/docs/DUCKDB_HTTP.md) | `make duckdb-clickhouse` |
| DuckDB → Iceberg → DuckDB/ClickHouse | [Native DuckDB maintenance](projects/duckdb-iceberg/docs/DUCKDB_MAINTENANCE.md) | `make duckdb-maintenance` |
| ClickHouse → Iceberg; hot/cold archive handoff | [Architecture guide](docs/ARCHITECTURES.md) | `make test ARCH=ch-archive` |
| DuckDB → Iceberg archive | [Architecture guide](docs/ARCHITECTURES.md) | `make test ARCH=duck-archive` |
| Three ClickHouse shards, three copies per shard, backed by Iceberg | [Architecture guide](docs/ARCHITECTURES.md) | `make test ARCH=ch-sharded` |
| Storage, compactor and cold recovery | [Recovery notes](projects/cold-storage-recovery/docs/COLD_STORAGE.md) | See the guide's individual targets |
| Previous native ClickHouse connector failures | [Preserved findings](projects/duckdb-clickhouse/docs/DUCKDB_CLICKHOUSE_FINDINGS.md) | `make duckdb-clickhouse-native` |

## Quick start

Run from this directory with Docker Compose v2, Make and Python 3.11+.
Allow at least 20 GiB Docker RAM and 12 GiB free disk for the complete lab.
For the HTTP-only isolated variant, first run `export LAB_VARIANT=http`.

1. `make docker-up` — create the example's storage infrastructure.
2. `make up` — build the local workloads and start the selected services.
3. `make test` — run the selected example's correctness checks.
4. `make down` — remove the application containers; retain infrastructure/state as documented.
5. `make docker-down` — delete only this lab's infrastructure and owned volumes.

Existing failed fixtures have preservation gates. Do not reset them or bypass
cleanup guards. Raw evidence stays under ignored `results/`; builds stay under
ignored `.build/`. No new performance experiments are being run during cleanup.

## Project directories

Each project keeps its own controllers, tests, guides and reports together.
The root owns common deployment, builds and raw evidence.

| Project | Directory | Parent Make target |
|---|---|---|
| [DuckDB → ClickHouse ↔ Iceberg](projects/duckdb-clickhouse/README.md) | `projects/duckdb-clickhouse/` | `duckdb-clickhouse` |
| [DuckDB Iceberg maintenance](projects/duckdb-iceberg/README.md) | `projects/duckdb-iceberg/` | `duckdb-maintenance` |
| [Cold storage recovery](projects/cold-storage-recovery/README.md) | `projects/cold-storage-recovery/` | `cold-storage` |
| [Iceberg manifest interoperability](projects/manifest-interop/README.md) | `projects/manifest-interop/` | `manifest-io` |
| [Compactor recovery](projects/compactor-recovery/README.md) | `projects/compactor-recovery/` | `compactor-crash` |


## Directory map

```text
projects/
  duckdb-clickhouse/       HTTP and native-connector paths
  duckdb-iceberg/          Maintenance, conflicts and snapshots
  cold-storage-recovery/  Archive and storage recovery
  manifest-interop/       Cross-engine manifest checks
  compactor-recovery/     Compactor crash and recovery
  # Each contains its own scripts/, tests/, docs/ and reports/ as applicable.
app/                      Docker-built workload and native DuckDB runner
config/                   Database settings and contracts
   contracts/
deploy/                   Compose files and network-fault helper
   compose/
scripts/                  Common lifecycle, preservation and build helpers
tests/                    Common ownership and recovery-unit tests
docs/                     Lab architecture and unfinished-work plan
reports/architectures/    Compact architecture summaries
workloads/                Tracked source links and compactor upload gate
```

Read [the architecture guide](docs/ARCHITECTURES.md) for ports, resource limits,
measured results, limitations and scenario commands. The
[plan](docs/PLAN.md) records unfinished work. The root README is an index, not a
running log of every investigation.
