# Matched single-node metrics/log comparison

The selected **level-3 single-node pass is complete**: OTLP/native million-record
controls, live crash/witness recovery, four batch sizes, three first/warm query
trials and three fresh-state paced ingestion trials. [Comparison tables and
limits](docs/COMPARISON.md) and [recovery findings](docs/RECOVERY.md) retain the
measured successes and buffered ACK loss. Clustered/deeper coverage remains
[future work](#further-coverage); no overall equal-durability winner is implied.

## Quick start

1. `make docker-up` — build/check the pinned workload, check disk/budget and start owned infrastructure.
2. `make up` — create this generation's deterministic corpus plan.
3. `make test` — run million-record common OTLP and native ingestion/readback controls serially.
4. `make down` — reconcile every retained witness and stop this generation, keeping its volumes.
5. `make docker-down` — destroy only its verified containers/volumes/network and local workload-image tag.

Prerequisites: Docker Compose and Python 3.13 (tested). Backends use the same
**4 CPU / 4 GiB total** budget: GreptimeDB gets all of it; VictoriaMetrics and
VictoriaLogs get **2 CPU / 2 GiB each**. The identical Go workload gets **1 CPU /
512 MiB**. Only one backend runs at a time. At least 8 GiB of free Docker disk is
required before ingestion. No host ports, hosted services, cloud credentials or
real AWS resources are involved; runtime endpoints are restricted to owned
services on an internal network.

`make driver-control` and `make native-control` use 10,000 records per signal.
`make correctness` and `make native-correctness` use one million per signal.
`make verify-retained` reads saved input without replaying it. `make report`
curates actual evidence and refuses a healthy-baseline report unless both latest
million-record protocol cases passed. Lifecycle/fault controllers are exclusive.

## Data and architecture

**1,000,000 metric points = 10,000 series × 100 timestamps**, with exact integer
values represented as gauges, plus **1,000,000 log bodies**. Half the series/events
belong to one tenant; the rest span 99 tenants. Log payloads are 99% repetitive
280-byte strings and 1% 4,096-byte deterministic hexadecimal strings, plus the
same structured fields. This synthetic distribution is stated, not production-fitted.
Metrics have one-second spacing; log timestamps have millisecond spacing.

Both engines receive identical input within each case. The native million-record
case also reuses the OTLP case's payload seed and timestamps. Its run label is a
fresh namespace so append-only log profiles cannot overlap. Every expected metric
label/timestamp/value and every canonical log body/timestamp is checked, with
missing/duplicate detection. Known fixed service/scope label mappings are verified
explicitly. These controls do not cover histograms, delta temporality or traces.

Common path: official OTLP protobufs + gzip. Native metrics: official Prometheus
Remote Write protobufs + Snappy on both engines. Native logs: JSON array through
a checked-in Greptime pipeline versus VictoriaLogs NDJSON, both uncompressed.
Framing/encoding differences are separate profiles, not identical wire formats.

Greptime's owned raft-engine WAL has synchronous writes. Victoria's default
buffers are periodically persisted. HTTP acceptance, query visibility and crash
persistence are distinct milestones; current results do not equate these durability
contracts. No collector is hidden outside the budget: this first comparison writes
directly from the same application. The later full-stack comparison includes collectors.

## Observed results — 2026-11-28

[Curated evidence](reports/BASELINE.json) records both million-record protocol
cases, smaller controls, failed metadata expectations, actual source manifests,
resource windows and verified teardown receipts. All four engine/protocol pairs
passed **one million metric points and one million log bodies**, with **0 missing,
0 duplicates and 0 wrong values** in their scoped readbacks.

The following are **single finite-corpus diagnostics**, using batches of 1,000
and one closed-loop caller. They are not sustained/repeated benchmarks and do not
establish a capacity or durability winner. Loop time includes construction,
encoding and journal sync; HTTP percentiles time each 1,000-record request.

| Path | Engine/signal | Admission loop (s) | Request p50 / p99 (ms) | Raw encoded / wire bytes ratio |
|---|---|---:|---:|---:|
| otlp | greptime:metrics | 3.373 | 2.098 / 3.471 | 13.109× |
| otlp | greptime:logs | 6.990 | 3.873 / 5.170 | 11.310× |
| otlp | victoria:metrics | 1.724 | 0.480 / 2.628 | 13.109× |
| otlp | victoria:logs | 5.047 | 0.916 / 6.143 | 11.310× |
| native | greptime:metrics | 1.919 | 1.373 / 2.127 | 7.823× |
| native | greptime:logs | 5.622 | 2.619 / 4.866 | 1.000× |
| native | victoria:metrics | 0.911 | 0.362 / 1.468 | 7.823× |
| native | victoria:logs | 4.469 | 1.026 / 6.850 | 1.000× |

Hardware: Apple M4 Pro, ARM64 Docker Desktop VM, **11 CPUs / 24.416 GiB**, Linux
6.12.76, Docker 29.5.3. The underlying physical disk class was not verified.
Resource windows include both backends' serial execution and retained-data
preflight/readback; **they are not isolated per-engine resource comparisons**.

| Observed invocation | CPU core-s | Mean / sampled peak memory (GiB) | Disk read / write bytes | Network RX / TX bytes |
|---|---:|---:|---:|---:|
| otlp million-record control | 33.968 | 0.395 / 1.053 | 24,576 / 158,801,920 | 525,538,497 / 584,714,299 |
| native million-record control | 39.393 | 0.386 / 0.988 | 24,576 / 152,326,144 | 1,774,762,790 / 1,896,225,460 |

Per-role CPU utilization, memory, resets and native pre-exit tool cgroups remain
in the report/raw evidence. Disk allocation snapshots include WAL/index/metadata
and earlier controls; compaction and unequal prior failed attempts prevent a
normalized storage winner. Wire compression above is **not native on-disk codec
compression**; that ratio has not been collected. Device IOPS/latency/queue depth
are unavailable from the sampled Docker counters; missing values are not zero.
The report explains short-tool, first-setup and early phase-label coverage gaps.

Two generations completed verified teardown. Repeated `make up` was invoked
separately after an acknowledged control; independent readback still passed.
Retired fixture/witness hashes remain immutable and are checked before old data
is skipped. Destruction refuses a stale receipt or a receipt for another fixture.
The first million-record cases ran through independent targets after small driver
controls; the combined `make test` target invokes those same tested functions.

## Repeated ingestion and query results

[COMPARISON.json](reports/COMPARISON.json) and [the detailed method/tables](docs/COMPARISON.md)
record three fresh-volume trials, 60-second warmup and five-minute measured windows,
with independent old/recent readers. All **9,971,000 Greptime** and **10,168,000
Victoria** writer ACKs across warmup/measurement reconciled, with no missing,
wrong or ambiguous records. CPU/memory, disk activity/space, native/wire compression
and network measurements retain their scopes and unavailable counters.

| Median of three per-trial results | Greptime | Victoria |
|---|---:|---:|
| Paced metrics admission | 4,663.3 records/s | 4,750.0 records/s |
| Paced logs admission | 4,563.3 records/s | 4,663.3 records/s |
| Metric request p99, batch 1,000 | 9.791 ms | 3.729 ms |
| Log request p99, batch 1,000 | 11.591 ms | 3.192 ms |
| Backend + workload CPU, sampled measured window | 59.744 core-s | 44.334 core-s |
| Mean container memory, including workload/cache | 1.338 GiB | 0.379 GiB |
| Warm grouped metric-sum query | 106.457 ms | 12.133 ms |
| Warm grouped log-count query | 5.976 ms | 0.970 ms |

The writers wait 200 ms after each request: these are **paced rates, not maximum
capacity**. The 558 first/warm query executions all matched the independent
oracle; engine restarts retained host filesystem cache. Victoria's measured CPU,
memory and selected query costs were lower in this synthetic workload, while the
crash tests below expose its different buffered durability contract. Native
VictoriaLogs disk data-part compression was about **21.38×**; it excludes indexes
and is distinct from wire gzip or whole-volume allocation.

`make batch-screen`, `make query-screen`, `make benchmark` and
`make comparison-report` expose this bounded work. `benchmark` verifies and
recycles this example's own volumes between its three trials; original witnesses
remain in ignored results. Twelve Go checks and three cleanup guards pass.

## Live crash and recovery results

Greptime retained all **37,000 acknowledged metric/log records** in each of three
crash cases. VictoriaMetrics lost **47,000 / 95,000** acknowledged metrics in the
first crash and **48,000 / 96,000** in the repeat; VictoriaLogs lost **29,000 /
80,000** acknowledged logs in its crash. All were restored by explicit external
application-witness replay, with exact body readback. Buffered admission differs
from Greptime’s synchronous WAL contract; these results establish no equal-durability
performance winner. The first failed status is preserved. See the [numbers,
resource measurements and recovery runbook](docs/RECOVERY.md) and
[curated recovery report](reports/RECOVERY.json).

`make live-control`, `make crash-metrics`, `make crash-logs` and serial
`make test-production` expose the cases. Eleven Go checks and three offline
cleanup guards pass; the fault targets ran independently.

## Findings and limits

The first strict verifier rejected VictoriaMetrics' added `scope.name`; a later
retained-data check found that an omitted scope version becomes the literal
`unknown`, not an empty value. Native series metadata established that mapping.
Corrected readbacks validated already accepted input before any new corpus was
sent. The failed case and failed audit evidence remain; these were harness
expectation mistakes, not demonstrated backend corruption. One early native
status inherited an OTLP description; its executed configuration/source correctly
identify the native path, and the later status wording is fixed.

Single-node volumes and a single physical VM cannot establish host/AZ HA or
physical power-loss durability. Graceful process switches between backends are
not abrupt-loss tests. No replica/quorum, independent restore, pressure/soak,
retention or upgrade qualification is implied.

## Further coverage

- [x] Live writer/reader during abrupt process loss; classify successful,
  failed and ambiguous requests, reconcile acknowledged loss and recovery.
- [x] Batch 1/100/1,000/10,000 screen; one representative paced cell with
  three alternating trials at the same logical starting state and resource caps.
- [x] Independent analytical query oracles, engine-cold versus warm timing and
  explicit query-cache policy. Physical OS-cache-cold remains a separate unrun scope.
- [ ] Further storage qualification: comparable Greptime/VictoriaMetrics codec
  ratios, continuous peak allocation, device IOPS/latency and equivalent compaction
  states. VictoriaLogs native part compression is measured; wire gzip remains separate.
- [ ] Comparable clustered contract/topology profile, including WAL/object-store,
  metadata, routers and all replication/fanout costs. Do not equate consensus
  replication with asynchronous copies merely because both have three processes.

## Files

`telemetry-workload` is a tracked-source symlink inside this comparison. Preparation
materializes regular files/hard links into ignored `.build/` for Docker. Source,
symlink and preparation are tracked; materialized files/caches never are. `config/`
owns native settings; `scripts/` controllers/reports; `tests/` destructive-cleanup
guards; `reports/` curated outcomes. Raw requests, cgroups, fixture generations and
teardown receipts stay under ignored `results/`.

Read [the contract and pinned source research](../docs/CONTRACT.md) for protocols,
whole-stack accounting and semantic differences. Source versions: GreptimeDB
**1.3.1**, VictoriaMetrics **1.157.0**, VictoriaLogs **1.55.0**, Go **1.27.4**,
OTLP protobufs **1.11.1** and Prometheus protobuf module **0.316.0**. Image digests
and dependencies are pinned; no custom wire-format or Java helper was written.
