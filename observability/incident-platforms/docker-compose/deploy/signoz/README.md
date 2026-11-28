# Native SigNoz — bounded single-node evaluation

Select `export PLATFORM=signoz` in the [parent lab](../../README.md); that lab
owns the five-command lifecycle, state and reports. This is a deployment component,
not an independent example. **Performance depth: 3.**

The deployment ran native SigNoz **0.152.0**, collector **0.144.19**,
ClickHouse/Keeper **26.11.4.11**, PostgreSQL **16.16**, and the vendor's histogram
helper **0.0.1**, pinned by image/artifact digests. Configuration derives from
[Foundry 0.5.0](https://github.com/SigNoz/foundry/tree/v0.5.0/docs/examples/docker/compose/pours/deployment).
PostgreSQL uses the latest patch of the vendor's supported major, not the latest
global major. Current ClickHouse replaces Foundry's older version: this is a local
compatibility experiment, not a vendor-supported production combination.
The helper remains the vendor-selected artifact. Its Dockerfile currently supports
ARM64 only. All services use the isolated internal network and lab-only credentials.

## Executed outcomes

The small control reconciled **10,000 metrics, 10,000 logs and 300 spans**.
Healthy case [8e8139a3](../../reports/8e8139a3f08d4121a27bd12d4d4e6893.json)
reconciled **1 million metrics, 1 million logs and 30,000 spans**, with zero missing
ACKs or duplicate returned records. It used the shared canonical fixture.
Native logical values pass; this is not a lossless OTLP round trip: event integer
attributes become strings, trace links become references without original link
attributes/state/flags, and metric timestamps have millisecond precision.

Collector crash [cbc2a89b](../../reports/cbc2a89b2b494386a9217dc77acf35b6.json)
held the collector down for **10 seconds**, proved exit 137 without OOM, kept other
native roles and workload actors live, and failed the **120-second** old/recent
visibility-recovery deadline. Partial publication was fenced without replay/reset.
The corrected independent readback found:

| Signal | Acknowledged | Present | Missing ACKs | Ambiguous, absent at audit cut |
|---|---:|---:|---:|---:|
| Metrics | 603,000 | 600,000 | **3,000** | 54,000 |
| Logs | 585,000 | 576,000 | **9,000** | 53,000 |
| Spans | 18,270 | 18,060 | **210** | 1,620 |

The native collector batches before exporting; queues are disabled. The evidence
is consistent with pre-export buffering loss, not proof that ClickHouse lost an
acknowledged synchronous commit. This configuration fails the lab's source-ACK
survival gate. All original data and failed reports remain; services were stopped
with `make preserve-stop`. Retained-state `make up` and a separate `make test`
passed for the original small control. Destruction is deliberately unqualified
and refuses to delete state; the five-command lifecycle is therefore incomplete.

## Diagnostic resource and timing observations

One paced healthy run, not a capacity benchmark or a performance winner:

- Whole native backend: **137.437 CPU core-seconds** during the sampled publication
  window. Sampled aggregate memory, including workload/measurement actors:
  **3,207.6 MiB mean / 3,935.4 MiB peak**. Backend budget: **8 CPU / 12 GiB**.
- Backend disk counters: **9,494,528 bytes read / 539,226,112 bytes written**.
  Owned volume allocation grew **262,610,944 → 766,816,256 bytes**, including retained
  fixtures, metadata and coordination state. Neither value is a codec ratio.
- HTTP batch admission p50/p99: metrics **1.064/3.573 ms**, logs **2.847/8.159 ms**,
  spans **0.490/3.081 ms**. These are 1,000-record metric/log batches and 30-span
  batches, not per-record durable commit times.
- Source protobuf gzip ratios: metrics **23.047×**, logs **17.027×**, spans **5.122×**.
  Native on-disk codec ratio was not captured and remains **unavailable**.
  Device IOPS, queue depth and I/O latency were not exposed by the collected Docker
  counters; they are unavailable, not zero. Raw CPU/memory/network/disk observations
  and sampling limits are retained in the curated reports.

## Findings, harness corrections and remaining gaps

Native PostgreSQL agent cleanup reports `DISTINCT`/`ORDER BY` SQLSTATE **42P10**;
startup also warns that the active-query logging directory cannot be created.
Both were nonfatal in this run and remain unresolved. A native numeric-attribute
filter using an integer literal fails ClickHouse **170 BAD_GET**; the explicit
floating literal and native JSON attribute path pass. Attribution is unisolated.

The first crash metric audit hit the verifier's metadata row cap: restarted
collectors serialized equivalent label maps in different orders. The verifier now
pages by logical series and validates every representation; the original failed
audit is preserved beside the corrected audit. A read-only disk walk also raced a
removed ClickHouse temporary insert directory. The shared sampler now reports
files disappearing during a live scan instead of aborting on that expected race;
the failed run's missing disk cut remains a measurement gap, not invented data.

No HA, Kubernetes, backend restart, power/disk loss, independent restore, retention
reclamation, upgrade or repeated performance qualification was run. Keeper has one
member and ClickHouse has one copy despite native ReplicatedMergeTree schemas.
