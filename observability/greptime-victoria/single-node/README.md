# Matched single-node metrics/log comparison

Pass 1 is complete: exact healthy ingestion controls and two verified lifecycle
generations ran. **This is not yet a qualified performance/recovery comparison.**
Live abrupt-loss tests, batch screens, cold/warm queries and sustained repeated
trials remain in the [follow-up list](#remaining-level-3-work).

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

## Remaining level-3 work

- [ ] Live writer/reader during abrupt process loss; classify successful,
  failed and ambiguous requests, reconcile acknowledged loss and recovery.
- [ ] Batch 1/100/1,000/10,000 screen; one representative sustained cell with
  three alternating trials at the same logical starting state and resource caps.
- [ ] Independent analytical query oracles, engine-cold versus warm timing and
  explicit query-cache policy. Physical OS-cache-cold remains a separate unrun scope.
- [ ] Native storage/compression/IOPS observability and equivalent quiescence,
  without interpreting wire compression as database codec compression.
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
