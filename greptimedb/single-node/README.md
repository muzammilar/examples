# GreptimeDB — single-node telemetry baseline

One GreptimeDB process with a durable local WAL, compared with single-node
VictoriaMetrics and VictoriaLogs on identical deterministic metrics and event-time logs.

## Quick start

1. `make docker-up` — build the client/probe and start all three database services.
2. `make up` — create the event-time pipeline and ingest one million samples/logs per backend.
3. `make test` — verify every sample, log body and timestamp against the external fixture.
4. `make down` — remove tables/pipeline and the Victoria datasets, keeping services running.
5. `make docker-down` — remove this project's services, volumes, network and built images.

Requires Docker Compose and enough Docker RAM for three 2 GiB database caps plus
one 2 GiB client. Every database has 2 CPUs / 2 GiB; the client has 2 CPUs / 2 GiB.
No ports are published. Defaults `SERIES=10000`, `POINTS=100`, `LOGS=1000000` are
Make variables. Use the full cleanup lifecycle before reseeding; old result files
are archived when a new empty database is seeded.

## Setup and semantics

GreptimeDB 1.3.1 uses the native local `raft_engine` WAL with `sync_write=true`.
VictoriaMetrics 1.156.0 and VictoriaLogs 1.54.0 use their own native local storage.
These admission/durability paths are not identical. No standalone service has a
replica or automatic failover. A single durable WAL is not a distributed quorum.

The local `telemetry-workload/` contains this example’s Go application and schemas.
This baseline has one region per Greptime table. Complete body/value/time readback
is required on all backends; ETags, counts or HTTP success alone are insufficient.

## Results

2026-11-15, Apple M4 Pro, Docker Linux aarch64, 11 CPUs / 24.42 GiB RAM;
Compose 5.1.4, native ARM64. Tests ran sequentially; unrelated host workloads were not isolated. Each backend
passed **1,000,000 metric samples and 1,000,000 log events**, with **0 missing,
0 duplicate and 0 wrong values**. Workload and infrastructure cleanup both passed.

### Bounded ingestion API timing

Rates below use successful HTTP-request-active time, excluding body generation
and fsynced external journals. They describe API admission during a short synthetic
historical backfill, not sustained end-to-end throughput or equivalent durable
commits. Metrics use 100 requests × 10,000 samples; logs 1,000 × 1,000 events per
backend. All 2,200 requests succeeded. Payloads are deterministic/compressible.

| Signal | Greptime per request-active s | Batch p50 / p99 ms | Victoria per request-active s | Batch p50 / p99 ms |
|---|---:|---|---:|---|
| metrics | 1,117,541 | 7.961 / 15.133 | 9,349,260 | 0.953 / 2.069 |
| logs | 255,214 | 3.129 / 9.522 | 726,668 | 0.735 / 12.762 |

### Warm matched PromQL

One warmup and 20 measured requests per query/backend; p99 is the maximum of this
small sample. All vectors are checked against independently computed expected values.

| Query | Greptime p50 / p99 ms | VictoriaMetrics p50 / p99 ms |
|---|---|---|
| `sum(fleet_load)` | 64.07 / 66.61 | 9.20 / 12.68 |
| `sum by (tenant) (fleet_load)` | 67.33 / 70.65 | 9.84 / 16.42 |
| `max_over_time(fleet_load[60s])` | 90.39 / 100.41 | 33.34 / 35.76 |

Raw results are ignored under `results/`. These measurements do not establish
hardware power-loss durability, production capacity, retention/compaction under
steady load, cold-cache latency or replicated failover. See the cluster example
for real continuous-load datanode failures, disk replacement and region migration.

## Sources

- [Pinned standalone configuration and WAL controls](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/config/standalone.example.toml).
- [Architecture](https://docs.greptime.com/user-guide/concepts/architecture/), [event-time pipeline configuration](https://docs.greptime.com/reference/pipeline/pipeline-config/).
- [Related research and cluster gotchas](../docker-compose-cluster/#sources-inspected).

## Source and build

`telemetry-workload/` is a tracked symlink to this system’s workload source.
The first lifecycle command runs `prepare.py` to materialize regular files in
`.build/telemetry-workload/`, which is the local Docker build context. `.build/` is
gitignored and is never committed. The application runs as this example’s
Compose service or Kubernetes workload Pod; its lifecycle needs no sibling
example’s running services or generated state.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
