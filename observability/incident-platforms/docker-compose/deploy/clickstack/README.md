# Native ClickStack component — bounded single-node evaluation

Selected through the parent lifecycle with `PLATFORM=clickstack`. This is one
profile of the parent-owned isolated lab, not an independently managed cluster.
The parent owns the internal network, source materialization, state and results.
Use `PLATFORM=clickstack make docker-up`, then `make up`, `make test`, `make down`
and `make docker-down` with the same `PLATFORM=clickstack` selection each time.
The normal down gate now refuses the known failed ACK case. `PLATFORM=clickstack
make preserve-stop` stops native services while retaining the failed volumes;
project-wide destruction remains blocked and unrun.
No host ports or real cloud accounts/credentials are used.

| Native role | Pinned version | CPU | Memory |
|---|---|---:|---:|
| ClickStack app/API/UI | 2.47.0 | 1.5 CPU | 2 GiB |
| Native collector/supervisor | 2.47.0, paired OTel 0.165.0 | 1.5 CPU | 2 GiB |
| ClickHouse | 26.11.4.11 | 4 CPU | 7 GiB |
| MongoDB metadata | 9.0.8 | 1 CPU | 1 GiB |

All roles count toward **8 CPU / 12 GiB**, plus the common workload budget.
Images are native ARM64, pinned by digest in `compose.yaml`. Native app readiness,
retained metadata registration/login, connections/sources and ingestion-auth
probes passed: missing authorization **401**, valid lab token **200** on an empty
OTLP request. The small control admitted **10,000 metrics, 10,000 logs and 300
spans**; full independent value qualification and the failed collector-crash result are recorded below.

The [official 2.47.0 Compose](https://github.com/ClickHouse/ClickStack/blob/%40hyperdx/app%402.47.0/docker-compose.yml)
pins MongoDB 5.0.32 and ClickHouse 26.10.3.7. This local **compatibility experiment**
uses the current stable releases above; successful metadata CRUD does not establish
an upstream-supported production combination. The first custom users file omitted
ClickHouse's required `default` account and exited 232 before workload input. The
original configuration/logs are retained. Correcting the example to include a
loopback-restricted default account started the same volume. A controller initially
rejected the native login's valid **303** redirect; correcting that reader made
retained login pass without resetting MongoDB. These were example/controller
errors, not demonstrated database failures.

## Native data and acknowledgement boundaries

The [native collector config](https://github.com/ClickHouse/ClickStack/blob/%40hyperdx/app%402.47.0/docker/otel-collector/config.yaml)
batches up to 10,000 records with a five-second timeout before export. Its
[OpAMP configuration](https://github.com/ClickHouse/ClickStack/blob/%40hyperdx/app%402.47.0/packages/api/src/opamp/controllers/opampController.ts)
controls the receiver, token checks, native tables and exporters. A source OTLP
ACK therefore does not establish downstream storage. The supervisor's persistent
volume preserves configuration/identity; **it is not a telemetry WAL**. Measure
live collector failure before making an ACK-survival claim.

Native schemas/samples show:

- Log body, nanosecond timestamp, trace/span IDs, numeric severity, resource and
  instrumentation scope are available. The native processor **lowercases severity
  text** and parses body JSON into additional attributes; original text casing and
  numeric attribute types are not lossless. Observed-time is absent from this schema.
- Gauge values, unit `us`, labels and scope are available, but `TimeUnix` is
  **DateTime with whole-second precision**. This corpus has whole-second metric
  points; subsecond metric fidelity is not qualified.
- Trace state, scope, start/duration, parents, events and links are exposed. Native
  attribute maps store strings; main-span and link flags are absent from these
  tables. A logical projection is not a lossless typed OTLP export.

`clickhouse.xml` follows the native product configuration with bounded caches,
server memory, local service DNS and console logging. `users.xml` constrains query
CPU/memory and the lab account to the owned network. Native exporter batching and
schema creation are retained; source batch size is not ClickHouse INSERT size
(`insert-batch-size`, `decision-ingestion-strategy`). See
[ClickHouse insert strategy](https://clickhouse.com/docs/best-practices/selecting-an-insert-strategy).
Read back effective settings and native parts before interpreting a benchmark.

This single-host profile does not establish HA, host/disk failure, partitions,
upgrades, backup/restore or production security. Those deployment/operational
follow-ups remain future work, separately from this bounded level-3 comparison.

## Measured bounded level-3 result — 2026-11-28

The healthy case `7957dd7b3a064fe4b6c974570015f9b1` passed all **1,000,000 metric
ACKs, 1,000,000 log ACKs and 30,000 span ACKs**. All **3,000 source protobuf
batches** matched the synchronous OpenObserve reference byte-for-byte, including
IDs, event times, batch boundaries and payload sizes; see
[canonical input proof](../../reports/CANONICAL_INPUT.json).

In native collector-crash case `ea4765f19b2f49bc9a775df084de006b`, SIGKILL exited
**137 without OOM**. Writers/readers stayed live through the ten-second hold;
ClickHouse, MongoDB and the app stayed up. The 120-second continuity gate failed
because accepted recent anchors remained missing. Later ingestion succeeded;
this does not mean the whole product was unavailable for 120 seconds.

| Signal | ACK records | Present after restart | Missing ACKs | Ambiguous records absent at cut |
|---|---:|---:|---:|---:|
| Metrics | 610,000 | 606,000 | **4,000** | 51,000 |
| Logs | 575,000 | 572,000 | **3,000** | 50,000 |
| Spans | 18,450 | 18,360 | **90** | 1,530 |

The publisher stopped after the failed deadline: this is a **partial fault run**,
not an attempted full million-record fault workload. Both actor exits were proved
clean before fencing. The original small control and older full healthy corpus
still reconciled after the fault. No replay, repair or reset occurred. Original
failed status, source ACK histories, missing-ID ranges, native container state and
volumes are retained. This fails the lab's ACK-survival gate; pre-export in-memory
batching is consistent with the observation. It is not evidence of ClickHouse
losing a synchronously committed mutation or a violated vendor power-loss promise.

| Diagnostic publication phase | Healthy full | Collector fault |
|---|---:|---:|
| Whole observed CPU | 131.037 core-seconds | 95.461 core-seconds |
| Mean aggregate memory | 2,888.680 MiB | 3,417.740 MiB |
| Peak aggregate memory | 3,403.516 MiB | 3,748.926 MiB |
| Docker read bytes | 856,064 bytes | 53,248 bytes |
| Docker write bytes | 570,499,072 bytes | 456,753,152 bytes |

These aggregate container counters include observed workload/observer roles;
phase durations and per-role utilization/deltas remain in the reports. They are
single-run paced diagnostics, not a throughput ranking. Verification CPU/I/O is a
separate phase. Device IOPS, I/O latency and queue depth are unavailable through
these Docker counters. Volume allocation was **299,098,112 → 855,359,488 bytes**
across healthy input, including metadata, indexes, WAL and retained state; a later post-failure audit snapshot measured **630411264 allocated bytes**.
That later observation is separate from the fault window and is not a transient
peak. Periodic snapshots remain in ignored evidence.

Healthy HTTP **batch admission** p50/p99 was **0.825 / 2.570 ms** for metrics,
**9.242 / 17.051 ms** for logs and **0.428 / 1.928 ms** for traces, each with
1,000 successful batches. These timings exclude encoding/journal fsync and do
not establish per-record latency, visibility or durable commit. Raw/gzip source
ratios matched the reference: **23.047× metrics, 17.027× logs, 5.122× traces**.

The native active-part snapshot reports **55,776,881 compressed bytes** versus
**1,433,846,837 uncompressed bytes** across native product tables, with
**153,323,912 part bytes on disk**. This includes retained controls, self-telemetry
and native indexes/tables; it excludes MongoDB/supervisor/WAL and is not a
whole-platform or isolated-corpus codec ratio. See
[parts, codecs and effective settings](../../reports/CLICKSTACK_STORAGE.json).
Native schema retention defaults to **720 hours**, unlike a normalized TTL/storage
comparison; all current fixture events are inside every tested retention window.

[Healthy evidence](../../reports/7957dd7b3a064fe4b6c974570015f9b1.json),
[failed collector evidence](../../reports/ea4765f19b2f49bc9a775df084de006b.json).
Further comparative query timing is blocked by this recovery gate; no qualified
cross-platform performance winner is claimed.

## Deployment gaps

**No ClickStack cluster or Kubernetes example has been implemented or tested.**
This profile has one ClickHouse and one MongoDB instance. Future coverage must
include supported replicated/sharded ClickHouse routing and schema creation,
Keeper quorum, metadata HA, correct shard/replica placement, native maintenance,
node/disk failures and verified recovery under live telemetry. App/collector
replicas alone do not establish storage replication or sharding. Keep that work
separate from this finished bounded single-node evaluation.
