# Observability

Future reproducible comparisons of telemetry backends, complete observability
stacks and infrastructure monitoring. The
[GreptimeDB/Victoria lab](greptime-victoria/single-node/) now has passing healthy
OTLP/native million-record controls, measured crash/witness recovery and three
matched fresh-state paced trials with first/warm queries. The [comparison](greptime-victoria/single-node/docs/COMPARISON.md)
reports workload-specific costs and unequal durability; it establishes no overall winner.
The [three-signal incident lab](three-signal-incident/) has passing full healthy
Greptime/Grafana controls and a preserved Victoria numeric-attribute type failure;
corrected full Victoria values reconcile. A later Loki restart omits 44,274
previously verified log ACKs; that unresolved failure blocks Grafana collector
qualification. Greptime collector recovery passed; Victoria value recovery passed with its
known failed type projection. The lab is stopped with failed data retained. The broader
[incident-platform comparison](incident-platforms/) is active, with all metadata/collector/UI roles
included. OpenObserve healthy native controls passed; the default-buffered crash
left **30 acknowledged spans missing**. Its separate synchronous-WAL reference
passed the bounded crash/readback gate; ClickStack/SigNoz/new Grafana profiles remain unrun.

The [per-system example plans](../docs/example-plans/streaming-and-observability.md#observability) specify the incident, failure and data checks for each component.

Starting reference: [ClickHouse — Top infrastructure monitoring tools comparison](https://clickhouse.com/resources/engineering/top-infrastructure-monitoring-tools-comparison).
It is a vendor-authored overview. Use it to identify candidates and testable
questions; verify capabilities with each project's own documentation/source and
measure our own results. Its rankings, pricing and performance claims are not
results from this repository.

## Account reminder

- [ ] **Before the Honeycomb comparison, remind Muzammil to create a Honeycomb
  account and select the free plan.** Check current quotas/retention first;
  the user creates the account and provides the intended test endpoint/key.
- Keep hosted exports opt-in, use synthetic telemetry, read keys from environment
  variables or ignored local files, and stay within the selected free allowance.
  Do not sign up for paid plans or assume a trial is an ongoing free tier.

## GreptimeDB vs Victoria: planned comparison

- [x] Focused level-3 **single-node GreptimeDB versus VictoriaMetrics/Logs**:
  common/native healthy controls, batch screen, three paced fresh-state trials,
  exact first/warm queries and live crash/ACK-loss recovery. The measured buffered
  and synchronous persistence contracts remain separate.
- [ ] Comparable clustered/HA profiles and a qualified equal-durability capacity
  comparison remain future work. The completed local observations do not qualify
  a whole Victoria stack or erase its measured buffered losses.

The existing Greptime examples benchmark
VictoriaMetrics and VictoriaLogs as baselines; they do not qualify a complete
Victoria stack or establish a fair whole-stack winner.

- Run single-node against single-node and supported HA/cluster against comparable
  HA/cluster topologies. Match the promised durability/failure tolerance, then
  compare at equal **total** CPU, RAM and storage budgets, including every WAL,
  metadata, object-store, collector and routing dependency. Report architectures
  that cannot meet the same durability contract separately.
- Use the same machine class, CPU architecture/model/count, RAM, disk type and
  capacity/IOPS limits, and network configuration. Match per-node sizes for
  comparable roles as well as the whole-stack budget above. Use identically sized
  generators and record every dependency; larger hidden WAL/storage machines
  would invalidate the comparison.
- Compare single-record ingestion with batches (initial sweep: **1, 100, 1,000,
  10,000 records/request**, recording payload bytes too), matched concurrency and
  compression. Exercise a common supported protocol and each engine's recommended
  native/bulk path separately. Include encoding/collector costs in end-to-end
  measurements; keep API-active throughput as a separately labeled metric.
- Test equivalent query approaches where supported, with independent result
  validation. Measure cold and warm execution separately, recording warmups and
  result/plan/engine/OS cache state. Do not label a process restart as a fully cold
  storage-cache run; use supported controls or an isolated owned VM. Precomputed
  aggregates and other unequal work belong in separate profiles.
- Replay identical metric samples and log bodies, tenant/cardinality/skew and
  out-of-order distributions, batch sizes, retention and query time ranges.
  Include traces only where the pinned engines support equivalent ingestion and
  queries; keep unmatched capabilities visible rather than scoring them as zero.
- Test both engines serially on an otherwise idle host, alternate execution order,
  use at least three independent repetitions, and publish every run plus the
  median/range. Freeze tuning before measurement and show both configurations.
- Measure sustainable ingestion at a stated latency/error target, API admission,
  end-to-end visibility and post-crash durability separately. Run matched warm
  and cold queries with correctness checks and enough samples to interpret p99;
  the earlier 20-query samples are not a capacity benchmark.
- Compare storage after equivalent compaction/quiescence and include all replicated
  data/WAL/metadata copies. Exercise live readers/writers during equivalent
  process, owned-disk and dependency failures, preserve ACK histories, and report
  outage, repair, data loss, operational steps and resource cost.

Publish workload-specific results and tradeoffs, not an overall winner inferred
from the earlier synthetic backfill numbers. This focused comparison precedes the
broader multi-vendor matrix below.

## Main stack comparison

Compare **ClickStack vs Victoria stack vs GreptimeDB vs OpenObserve vs SigNoz vs
Grafana stack**, using self-hosted community/open-source components and explicitly
recording dependency licenses and excluded commercial features.

| Stack | Planned runnable edition/components | Representative clustered example |
|---|---|---|
| [ClickStack](https://clickhouse.com/clickstack) | Self-hosted ClickHouse + OTel Collector + ClickStack UI; include its settings-store dependency and license in the deployment inventory | High-cardinality application telemetry, trace-ID lookups, SQL log/trace correlation, replicated storage recovery and collector backlog |
| [Victoria stack](https://docs.victoriametrics.com/victoriastack/ingestion/) | Community VictoriaMetrics + VictoriaLogs + VictoriaTraces | Use the same fleet metrics, logs and trace trees. Budget VictoriaMetrics native replication, [VictoriaLogs sender-side replication and buffering](https://docs.victoriametrics.com/victorialogs/cluster/), and [VictoriaTraces external fan-out](https://docs.victoriametrics.com/victoriatraces/cluster/) separately. Verify each destination after an outage. |
| [GreptimeDB](https://docs.greptime.com/user-guide/concepts/architecture/) (Rust) | Self-hosted upstream release with declared WAL, metadata and storage dependencies | Unified application telemetry, matching SQL/PromQL/trace queries, region/WAL recovery and high-cardinality ingestion |
| [OpenObserve](https://openobserve.ai/docs/) (Rust) | AGPL community code; qualify current edition limits and dependencies before pinning | SQL/log search, metrics and trace correlation; clustered ingestion/query/compaction and object-store recovery |
| [SigNoz](https://signoz.io/docs/architecture/) | Community code outside its separately licensed enterprise directory, with OTel and ClickHouse dependencies | Application monitoring, correlated signals, collector/ClickHouse failure and tenant queries |
| [Grafana stack](https://grafana.com/oss/) | Grafana OSS + Loki + Tempo, with a Prometheus baseline and a separate Mimir metrics scale-out profile | Verify Loki ingestion/compaction and trace completeness during queue, live-store, block-builder and historical object-store failures in the [Tempo 3 distributed architecture](https://grafana.com/docs/tempo/latest/introduction/architecture/). Include its Kafka-compatible queue in the budget. |

The original **GreptimeDB vs Victoria stack vs Prometheus + Loki + Tempo**
comparison remains the first shared-corpus baseline. Adding ClickStack, OpenObserve
and SigNoz extends that matrix. Mimir replaces the metrics backend only in a
named scale-out profile; do not silently substitute it for Prometheus. Cortex
and Thanos are additional metrics architecture experiments.

Check the selected source revision and every dependency's license. For example,
ClickStack's UI is [MIT](https://github.com/hyperdxio/hyperdx/blob/main/LICENSE),
OpenObserve publishes an [AGPL license](https://github.com/openobserve/openobserve/blob/main/LICENSE),
and SigNoz documents [community/enterprise license boundaries](https://github.com/SigNoz/signoz/blob/develop/LICENSE).
A stack using open-source libraries does not make every bundled dependency or
commercial service open source.

## Hosted comparisons, only where free access is available

Availability checked 2026-11-15; recheck before running. These are hosted service
comparisons, not self-hosted OSS deployments. No hosted experiment has run here.

| Product | Availability boundary | Potential experiment |
|---|---|---|
| [Honeycomb](https://www.honeycomb.io/pricing) | Published free plan; account setup reminder above. The hosted backend is not a downloadable OSS database | Same synthetic high-cardinality traces/events and investigation tasks; record sampling, retention, quotas, ingest visibility and query latency |
| [Better Stack](https://betterstack.com/pricing) | Published free personal-project telemetry allowances; quotas vary by signal. Hosted platform, not a self-hostable OSS edition | Same allowed logs/metrics/traces and service health checks, bounded to the verified free allowance |
| [Chronosphere / Cortex XCOR](https://www.paloaltonetworks.com/cortex/xcor) | No public ongoing free tier was verified for this plan. Keep the hosted comparison deferred until no-cost access is confirmed. | Apply a [shaping rule](https://docs.chronosphere.io/control/shaping) to high-cardinality telemetry and verify the remaining site-level totals and lost label detail. The self-hosted Cortex project above has its own metrics workload. |

Open-source collectors or samplers associated with a vendor are separate projects;
they do not constitute an open-source version of that vendor's hosted backend.
Do not fabricate a local Honeycomb, Chronosphere or Better Stack deployment.

## Infrastructure monitoring comparison

| System | Edition | Potential example |
|---|---|---|
| [Nagios Core](https://www.nagios.org/projects/nagios-core/) | Free GPLv2 Core, not commercial Nagios XI | Plugin-based host/service checks across a small fleet; process/network/disk-pressure faults, detection/recovery latency, missed/duplicate alerts and check overhead |
| [Zabbix](https://www.zabbix.com/license) | Self-hosted OSS release; current releases use AGPLv3 | Agents, proxies, discovery and supported server HA; proxy disconnect/backfill, server failover and database recovery with the same infrastructure fault schedule |

Use these systems for the infrastructure checks they actually support. Compare
common availability/detection/alerting tasks with the other stacks, and label
unsupported log/trace queries as unsupported rather than inventing equivalents.

## Separate system examples

Each system gets its own directory and supported deployment shapes: single-node,
Docker Compose cluster, official Kubernetes operator or Helm/StatefulSet fallback,
and a representative workload **IN A CLUSTER WHERE POSSIBLE**. Workloads may share
an existing cluster example. Unsupported modes and edition limits require sources.

| Signal | Separate examples | Potential workload |
|---|---|---|
| Metrics | Prometheus, VictoriaMetrics, Mimir, Cortex, Thanos | High-cardinality fleet metrics, late/out-of-order samples, range queries, HA scrape dedup, WAL replay and historical-block recovery |
| Logs | Loki, VictoriaLogs, Quickwit (Rust) | Structured/text queries, tenant skew, retention/compaction and ingester/indexer/storage recovery |
| Traces | Tempo, VictoriaTraces, Jaeger | Deterministic OTLP spans, trace-ID retrieval, supported filtering, sampling, collector backlog and storage loss |
| Unified | ClickStack, GreptimeDB, OpenObserve, SigNoz | Application incidents correlating metrics/logs/traces and business context, with whole-stack resource and recovery costs |
| Infrastructure | Nagios Core, Zabbix | Identical host/service failures and independent ground truth for detections, recovery and alerts |

Prometheus HA scrapers are independent databases; multiple scrapers do not create
native shared-database sharding. Qualify each trace engine's supported query/API
subset instead of assuming TraceQL, Jaeger APIs and SQL are interchangeable.

## Shared experiment contract

- One deterministic synthetic application and worker, with an OpenTelemetry
  Collector, known request/event/span IDs, event timestamps, tenant cardinality
  and explicit sampling. Store ground truth outside the tested stack.
- Independent ingestion runs from the same corpus; run stacks sequentially with
  declared whole-stack CPU/RAM budgets. Include metadata databases, queues,
  collectors, caches and object storage in topology and resource accounting.
- Verify complete values/bodies and matched query results, not just counts.
  Separate emitted, collector-accepted, backend-accepted, query-visible and
  post-crash-recoverable data. Document partial acceptance and ambiguous outcomes.
- Match retention, event-time ranges, filters, sampling and relevant durability
  settings. Explain unavoidable semantic differences and unmatched features.
- Measure ingestion/admission and end-to-end visibility, p50/p99 query latency,
  cold versus warm reads, storage/compression, cardinality/skew, compaction,
  backpressure and recovery. Report errors and missing/duplicate telemetry.
- Separate process crashes, permanent owned-disk loss and storage/network outages.
  Keep traffic running, reconcile acknowledged outcomes and verify native repair.
- For Loki/Tempo and other S3 consumers, deploy an example-owned local Garage
  or SeaweedFS service from the supported configuration. Record the shared physical host and disk.
- Publish exact versions, hardware, caps, workloads and numeric outcomes only
  after running the tests. Document gotchas, unsupported features and limits.

All runnable examples keep the five-command lifecycle in [AGENTS.md](../AGENTS.md)
and [FORMAT.md](../FORMAT.md). The account-backed optional checks must report
unavailable access as unrun; they must not block the self-hosted OSS experiments.
