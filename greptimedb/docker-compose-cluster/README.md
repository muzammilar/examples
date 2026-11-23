# GreptimeDB — clustered fleet telemetry

Three-region metrics and event-time logs on native GreptimeDB, with Kafka-compatible
remote WAL, replicated Garage S3 and an independent etcd metadata quorum. Matched
VictoriaMetrics and VictoriaLogs baselines receive exactly the same generated data.

## Quick start

1. `make docker-up` — start and configure the owned storage/WAL/metadata services and database roles.
2. `make up` — create schemas/pipeline; ingest the same million samples and million logs into both systems.
3. `make test` — reconcile every sample, log body/timestamp and all historical acknowledged recovery rows.
4. `make down` — remove the workload tables, pipeline versions and Victoria datasets, keeping infrastructure running.
5. `make docker-down` — remove this project's infrastructure, volumes, network and built images.

Requires ARM64 Docker (tested native aarch64), Compose with `include` support,
Python 3 and at least 24 GiB Docker RAM for the complete local comparison. No host
ports are published. Native dependencies and clients run in Docker; the host
orchestration uses Python's standard library. Full teardown precedes a clean rerun;
old results are archived when a new empty database is seeded.

## Setup

| Role | Count / selected version | CPU / RAM caps in aggregate |
|---|---|---|
| GreptimeDB | 3 datanodes, 3 Metasrvs, 2 frontends; 1.3.1 | 8 CPUs / 5.625 GiB |
| WAL | Redpanda 26.2.3, 3 brokers; 12 RF3 WAL topics | 3 CPUs / 3 GiB |
| Metadata | etcd 3.6.16, 3 members | 1.5 CPUs / 0.75 GiB |
| Object storage | Garage 2.5.0, 9 peers across 3 logical zones, plus HAProxy | 9.5 CPUs / 6.875 GiB |
| Metrics baseline | VictoriaMetrics 1.156.0: 3 vmstorage, vminsert, vmselect; RF3 | 3.5 CPUs / 2.375 GiB |
| Logs baseline | VictoriaLogs 1.54.0, **one process** | 1 CPU / 0.75 GiB |
| Client | Go, official Prometheus protobufs + Snappy | 2 CPUs / 2 GiB |

These are different durability geometries and whole-stack budgets, not a normalized
product ranking. VictoriaLogs is explicitly a single-process baseline; this is not
the full future [three-signal stack comparison](../../observability/README.md).
All services and logical copies share one physical computer/Docker disk.

Greptime regions have one active datanode owner in this configuration. Durability
comes from the independent RF3 WAL and three-zone object storage; do not describe
three datanodes as three hot replicas of every region. Native region routing is
checked before and after migration/failure. Frontends route queries and writes;
Metasrv owns metadata, leases and failover; etcd supplies the metadata quorum.

## Workload and correctness

`SERIES=10000`, `POINTS=100`, `LOGS=1000000` are Make variables. Metrics have 10,000
hosts, 100 tenants and 100 one-second historical samples per host: **1,000,000
samples per metrics backend**. Values are deterministic host/point functions.
Logs have **1,000,000 unique event IDs per log backend**, known tenants, levels,
messages and event timestamps. The explicit native pipeline uses the supplied
millisecond event time, matching VictoriaLogs. Both Greptime tables have three
logical regions. This is a bounded backfill workload, not a continuous production
capacity test.

`make test` reads every metric value and log body/time from each backend. It rejects
missing, duplicate, stale/wrong values and unexpected recovery rows. Recovery tests
keep fsynced attempt/ACK journals outside database volumes; ambiguous batches are
optional exact-data outcomes and are never blindly replayed. Historical ACKs from
all previous cases are reconciled after later failures too.

The final fixture passed **1,000,000 samples + 1,000,000 logs per system** with
**0 missing, 0 duplicates and 0 wrong values**. Across four recovery/scaling cases,
**102,500 / 102,500 acknowledged rows** remained intact, with no unexpected rows.

## Measured results

2026-11-15, Apple M4 Pro; Docker Linux aarch64, 11 CPUs / 24.42 GiB RAM; Compose
5.1.4. The aggregate CPU caps exceed available cores. Tests were sequential within
this complete local topology, with unrelated host workloads not isolated. Each
query has one warmup and 20 measured requests; p99 is the maximum of this small
sample. Request timings include transfer, decoding and correctness checks.

### Ingestion API timing

The table divides samples/events by **time inside successful HTTP requests**.
Protobuf/JSON preparation and the external fsynced journal are outside that timer.
These short, compressible historical batches measure API admission, not sustained
end-to-end throughput or equivalent durable commits. In particular, Victoria
admission/visibility and Greptime remote-WAL acknowledgement have different
semantics. Complete readback is a separate requirement.

| Signal | Greptime samples/events per request-active second | Batch p50 / p99 ms | Victoria samples/events per request-active second | Batch p50 / p99 ms |
|---|---:|---|---:|---|
| metrics | 802,954 | 11.432 / 18.207 | 5,059,526 | 0.548 / 4.408 |
| logs | 305,786 | 3.191 / 4.893 | 572,866 | 0.766 / 20.249 |

Metrics use 100 requests of 10,000 samples; logs use 1,000 requests of 1,000
events per backend. All **2,200 ingestion requests** returned success. Payloads
are deterministic and compressible, not an incompressible-data benchmark.

### Matched PromQL

Each returned vector is checked against independently generated expected values;
both engines query the same evaluation time and input samples. Warm results:

| Query | Greptime p50 / p99 ms | VictoriaMetrics p50 / p99 ms |
|---|---|---|
| `sum(fleet_load)` | 74.92 / 87.90 | 28.11 / 32.50 |
| `sum by (tenant) (fleet_load)` | 73.30 / 75.40 | 30.08 / 33.36 |
| `max_over_time(fleet_load[60s])` | 206.60 / 215.29 | 53.16 / 56.68 |

Independent baseline readers stayed live through each operation. Across the four
cases they observed 93 request errors and zero missing/wrong known-body reads.
Full reconciliation retained all 102,500 ACK rows; 34 additional attempted but
unacknowledged rows were also stored. Failed responses do not imply absent writes.

## Recovery and scaling experiments

Run these before the two cleanup commands. Each has an independent journal and
full baseline/historical reconciliation; the producer remains running through
native failover/recovery or migration.

| Command | Native operation | Observed result |
|---|---|---|
| `make failover` | Kill owning datanode; retained disk | 37,500 ACK rows; 18,500 during 30.35 s hold; routes ready 36.23 s; full check 59.75 s; 1 ambiguous batch; 45 read errors |
| `make disk-failure` | Fence owner; replace exact owned cache volume | 29,000 ACK rows; 9,500 during 30.30 s hold; routes ready 36.44 s; full check 60.48 s; 2 ambiguous batches; 48 read errors |
| `make scale-out` | Add owners and migrate regions, 3 → 5 | 8.566 s migration; 21,500 ACK rows; 0 ambiguous batches/read errors |
| `make scale-in` | Drain added owners, 5 → 3 | 0.829 s migration; 14,500 ACK rows; 0 ambiguous batches/read errors |

Failure timers begin at injection and include the deliberate hold. Failover was
confirmed at the first placement check after the 30-second hold; this does not
measure the exact earlier election/migration completion instant. Every case had
**0 lost acknowledged rows, 0 duplicates and 0 wrong values**. The process/disk
cases also preserved all four checked survivor container IDs.

## Gotchas reproduced

- Distributed region failover requires shared storage and remote WAL. The pinned
  release labels object-store WAL experimental/unsupported; this example uses
  Kafka-compatible WAL, never `noop` or failover with local-only WAL.
- Redpanda's development profile enables write caching. Setup explicitly disables
  `write_caching_default` before data ingestion; native topic descriptions show
  RF3 and write caching disabled. Do not equate Kafka-compatible APIs with identical
  persistence/ISR semantics; Redpanda uses its native Raft replication path.
- Imported Compose files share a volume namespace. A first disk attempt was
  rejected because the old volume was still mounted by Garage. All ACKs were
  reconciled, then the fixture was rebuilt. Greptime now uses `gt-data*`/`gt-meta*`
  volumes; native labels are checked before destructive removal.
- Greptime reserves identifiers such as `id`, `level` and `message` in DDL.
  Use explicit names/quoted schema fields. The log pipeline's rename is one field
  string (`"epoch_ms,event_time"`), not two timestamp fields.
- Pipeline deletion requires the native version. A missing pipeline returns an
  exact HTTP 400 error in this pinned release; only that known absence is accepted
  during cleanup. Other errors fail the command.
- The etcd and VictoriaLogs images do not have a shell. Health checks use native
  etcdctl and a built HTTP probe respectively. Victoria's wget probe uses IPv4
  explicitly because `localhost` selected an unavailable IPv6 listener here.
- Fetching one million logs with an implicit sorted limit exceeded VictoriaLogs'
  92 MiB query budget. Verification uses bounded 10,000-event time windows and
  still checks all rows, without increasing the server memory cap.

## Limits

This qualifies datanode crashes, empty local-disk replacement and region migration,
not correlated host power loss, two WAL-broker failures, metadata-quorum loss,
full disks, network partitions, backup restore, WAN behavior or the independent
Victoria backends' full fault matrices. Synthetic backfill, warm queries and
small request samples are not capacity planning. Private HTTP, fixture credentials
and one physical host need production hardening. No trace comparison has run.

Raw fixtures, native routes, resource inventory, journals, rejected attempts and
results are ignored under `results/`; measured final values above come from the
matched-event-time fixture, not an earlier identity-pipeline run.

## Sources inspected

- [Architecture and durability components](https://docs.greptime.com/user-guide/concepts/architecture/), [table sharding](https://docs.greptime.com/user-guide/deployments-administration/manage-data/table-sharding/), [native ADMIN operations](https://docs.greptime.com/reference/sql/admin/).
- Pinned native configuration: [datanode](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/config/datanode.example.toml), [Metasrv](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/config/metasrv.example.toml), [frontend](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/config/frontend.example.toml).
- Pinned [Kafka log store](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/log-store/src/kafka/log_store.rs), [producer](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/log-store/src/kafka/producer.rs), [worker](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/log-store/src/kafka/worker.rs), [Prometheus HTTP implementation](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/servers/src/http/prometheus.rs).
- [HTTP endpoints](https://docs.greptime.com/reference/http-endpoints/), [pipeline configuration](https://docs.greptime.com/reference/pipeline/pipeline-config/), [1.3.1 release fixes](https://github.com/GreptimeTeam/greptimedb/releases/tag/v1.3.1).
- [VictoriaMetrics cluster replication and deduplication](https://docs.victoriametrics.com/victoriametrics/cluster-victoriametrics/), [VictoriaLogs querying](https://docs.victoriametrics.com/victorialogs/querying/), [native log deletion](https://docs.victoriametrics.com/victorialogs/#how-to-delete-logs).

## Source and build

`telemetry-workload/` is a tracked symlink to this system’s workload source.
The first lifecycle command runs `prepare.py` to materialize regular files in
`.build/telemetry-workload/`, which is the local Docker build context. `.build/` is
gitignored and is never committed. The application runs as this example’s
Compose service or Kubernetes workload Pod; its lifecycle needs no sibling
example’s running services or generated state.

`make test-production` runs correctness, process crash, empty-cache replacement,
scale-out, scale-in and final historical reconciliation serially. Faults require
a fresh lifecycle when their prior result marker exists.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
