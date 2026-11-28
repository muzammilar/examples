# Three-signal checkout incident lab

**Restart qualification failed:** Loki omits 44,274 previously verified log ACKs
after retained stop/start. No collector fault was injected into Grafana; data
remains preserved. [Failure evidence](../docs/FINDINGS.md).

Level 3 local comparison. Small typed controls passed on Greptime and Grafana;
Victoria's experimental Tempo API changes integer attributes to strings. The full
Greptime and Grafana corpora passed; Victoria
reconciles the full corpus only with its failed type projection. Collector
recovery remains in progress.
See [measured findings](../docs/FINDINGS.md), including preserved failed runs.

## Quick start

Prerequisites: Docker Compose, Make and Python 3.10+ for controllers. The Go
workload builds, vets and tests inside Docker; no host Go toolchain is required.
Reserve 8 CPU / 11 GiB in Docker and at least 8 GiB free disk space.

1. `make docker-up` — build the workload, pull pinned images and prepare owned infrastructure.
2. `make up` — initialize or verify the example's experiment plan.
3. `make test` — run exact native readback with live writers/readers; declared fidelity violations fail.
4. `make down` — reconcile frozen journals, then stop owned services and retain storage.
5. `make docker-down` — require a current verified receipt before removing owned infrastructure/state.

`make test` first establishes the small driver control on a clean checkout and
stops on its first contract violation. `make driver-control` runs that initial 10,000-metric / 10,000-log / 300-span mapping
control explicitly. It precedes the million-record case. Re-running publication over an
existing control is refused: use `make resume-control CASE=<id> BACKEND=<name>`
for unrun profiles and frozen readback, not reseeding. The million-record diagnostic requires that control's preserved input and readback. The observed Victoria type failure
must be inspected with `make diagnose-projection CASE=<id>` before a separately
labeled value/recovery diagnostic proceeds. This diagnostic never changes the
failed exact-type verdict.

## Separate scenarios

- `make collector-crash`: kill the collector, hold it down for 10 seconds with
  publishers/readers live, restart the same container and reconcile frozen ACKs.
- `make verify-retained`: read-only logical value/history audit of all retained cases.
- `make resume-experiment CASE=<id>`: reconcile existing fenced inputs and run
  previously unrun profiles; preserve the original failure and write a new status.
- `make correctness BACKEND=victoria`: a separately identified full healthy
  case for just that profile, useful after fixing an adapter. No source replay.
- `make test-production`: serial correctness then collector recovery; a contract
  failure stops qualification. The independent collector target remains a
  clearly labeled diagnostic for investigating the known failed trace contract.
- `make report CASE=<id>`: compact journal/readback summary without raw payloads.
- `make check`: materialize source, validate Compose and build with Go vet/tests.

A full fixture is 1,000,000 gauge points, 1,000,000 logs and 30,000 correlated spans.
Publication is paced, not a maximum-throughput benchmark. Native trace readers
check exact values, parentage, timestamps, attributes, events and links. Missing
ACKs, RPC failures, ambiguous source outcomes and changed types remain separate.

This example owns project `obs-three-signal-incident`, internal subnet
172.30.74.0/24 and its own backend/collector-queue volumes. It shares no state,
network, ports, controllers or lifecycle with the metrics/log comparison.
`incident-workload` links to this comparison's tracked Go source; preparation must
materialize it into ignored `.build/incident-workload` before Docker builds.

One profile runs at a time: GreptimeDB; VictoriaMetrics/Logs/Traces; or
Prometheus/Loki/Tempo. Total backend including collector: **6 CPU / 8 GiB**.
Publisher 0.75 CPU / 384 MiB plus live reader 0.25 CPU / 128 MiB; the one-shot
verifier runs after they stop with the same aggregate 1 CPU / 512 MiB budget.
All credentials/endpoints remain local. No AWS files, hosted exporters or MinIO.

The collector deliberately has no volatile batch processor ahead of its fsynced
persistent exporter queue: source OTLP requests are already batched. That still
cannot upgrade a buffered backend ACK into durable end-to-end delivery. Physical
duplicates, logical completeness and wrong answers need separate measurements.

Configuration choices are documented in [the contract](../docs/CONTRACT.md).
Loki's explicit 8 MB/s ingestion/stream ceiling avoids silently comparing a
low default quota against an unthrottled endpoint; actual offered/admitted rates
must still be recorded. These are unreplicated local profiles, not production HA.

The workload also exposes the repository-tracked, read-only volume inspector
through `storage-observer.go`; preparation resolves that source link into a regular
Docker-context file. It uses no sibling example state, binaries or Make targets.
The materialized context remains ignored.

## Limits and future work

These are **single-copy, single-host profiles**, not replicated production
clusters. A collector restart with retained storage does not establish database
crash durability, node/disk-loss tolerance, partition behavior or independent
restore. Those are future deployment-specific cases, outside this level-3 batch.

Native backend admission policies differ. The collector's fsynced queue does not
make a downstream buffered ACK synchronous storage; an OTLP success is reconciled
against native readback rather than assumed durable. Victoria's type failure blocks
exact typed trace qualification even when its projected values reconcile. Span
sampling flags are outside this initial common oracle; [the contract](../docs/CONTRACT.md)
records that explicit boundary. Deduplicating query APIs do not establish the
absence of physical duplicate storage.

The publisher measures batch HTTP admission in ms; the live reader measures
individual native point-query duration and sampled ACK age. Full-oracle times
include fetching and verification of many queries and are labeled separately.
The workload is closed-loop and deliberately paced; there is no maximum-throughput
or long-tail capacity winner from these single functional runs. Cold/warm repeated
query comparisons are future work here; completed metrics/log-specific comparison
results live in the [separate lab](../../greptime-victoria/).

The observer records every running owned role at approximately 1 Hz, including
CPU utilization/core-seconds, cache-inclusive memory, network and available disk
read/write bytes. `scripts/resource_report.py results/resources-<id>.jsonl
live-publish:<backend>` selects the actual live phase. Native pre-exit cgroup
counters supplement short-lived actors. Disk snapshots run every 30 seconds and
before/after publication; they are sampled allocation, not an exact transient
peak. Missing device IOPS/latency/queue or native codec counters remain unavailable.
Gzip ratios cover only the source OTLP wire payload and are never called on-disk
compression. Raw evidence stays ignored; compact selected reports are tracked.

## Executed healthy controls

| Profile | Metric ACKs present | Log ACKs present | Span ACKs present | Selected typed fields |
|---|---:|---:|---:|---|
| GreptimeDB | 1,000,000 | 1,000,000 | 30,000 | Pass |
| Victoria, corrected reader | 1,000,000 | 1,000,000 | 30,000 | **Fail: integer attributes become strings** |
| Prometheus/Loki/Tempo | 1,000,000 | 1,000,000 | 30,000 | Pass |

Zero missing acknowledged IDs in these frozen healthy readbacks. Victoria's
original partial-publication case and exact-type failures remain failed; the
separate projected-value diagnostic never qualifies them as typed passes.
Collector crash/recovery is currently running and is **not yet a passed result**.

[Initial full/partial evidence](reports/HEALTHY_INITIAL.json),
[corrected Victoria evidence](reports/VICTORIA_HEALTHY.json),
[live Greptime resources](reports/GREPTIME_LIVE_RESOURCES.json),
[live Victoria resources](reports/VICTORIA_LIVE_RESOURCES.json),
[live Grafana resources](reports/GRAFANA_LIVE_RESOURCES.json),
and [sampled storage](reports/STORAGE.json) retain scopes and gaps.
[Findings](../docs/FINDINGS.md) explain adapter mistakes, visibility, type loss and
verified counts. Eight Go tests/vet and five Python safety tests pass.
