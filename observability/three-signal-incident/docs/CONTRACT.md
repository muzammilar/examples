# Common incident contract

This contract defines the tests; [findings](FINDINGS.md) distinguish executed
evidence and remaining work. Keep the selected level-3 scope: one
healthy corpus, collector crash/recovery with live publication, native query
correctness and bounded query-latency observations. Do not repeat a broad
capacity/durability matrix for every new stack. Those comparisons remain future
work until correctness/recovery is established.

## Shared incident

10,000 synthetic checkout requests, three spans per request, 100 log records per
request and 100 metric points per series: **30,000 spans, 1,000,000 logs and
1,000,000 gauge points**. Tenant 0 owns half the requests; the rest span 99 tenants.
Requests 4,000–4,499 for tenant 0 form the payment incident: 250 failed requests,
500 error spans and 25,000 error logs. Their metric series spike during seconds
40–44. This is a deterministic incident generator, not a measured business
application or auto-instrumentation overhead benchmark. The complete fixture,
seed, timestamps, payload bytes and IDs are identical across backends.

Source traces include parents, names, kinds, status/messages, resource and span
attributes, one event and a link. Choose microsecond-aligned times so a Jaeger
API's microsecond representation is a comparable common subset. Store and verify
trace IDs, all span IDs/parents, durations, status, chosen attributes and event/link
fields where the pinned native API exposes them; explicitly report any lossy or
unsupported projection. Do not infer complete trace fidelity from a trace count.
Logs preserve canonical complete bodies, severity, timestamp, tenant and trace/span
correlation. Metrics preserve every sample, label set, timestamp and value. Keep
high-cardinality request IDs out of metric labels and index/stream keys.

## Budgets and paths

Run one profile at a time. Total backend: **6 CPU / 8 GiB**, including a
0.5 CPU / 512 MiB collector. Greptime receives the remaining 5.5 CPU / 7.5 GiB.
The split profiles allocate metrics 2 CPU / 3 GiB, logs 2 CPU / 3 GiB, traces
1.5 CPU / 1.5 GiB. Workload/verifier: 1 CPU / 512 MiB, identical on every profile.
Verify these effective limits and include queues/WAL/index/metadata in space use.
No UI is required for this backend comparison; the broader incident-stack project
must separately budget its UI, settings store and every other dependency.

Use common OTLP binary HTTP/gzip with a pinned native collector. Use persistent
`file_storage` with fsync enabled and bounded exporter queues; do not place an
in-memory batch processor ahead of that persistence while implying durable receiver
ACKs. Collector admission still does not prove backend persistence. Export retries
and lost responses can create duplicates; declare at-least-once delivery and
measure physical duplicates separately from complete logical IDs and wrong values.
An independent source journal remains required. Do not silently deduplicate a
report to hide physical loss or duplicate delivery.

Prometheus uses its supported OTLP receiver for the common push profile; scrape
semantics are a separate future profile. Loki uses native OTLP structured metadata
with conservative stream labels, not a stream per request. Tempo monolithic mode
supports local development storage without Kafka; its production microservices
mode needs Kafka-compatible WAL and object storage. This lab must not call local
filesystem/monolithic mode an equivalent production HA architecture.

## Failure and evidence

Keep all three publishers active during a ten-second collector SIGKILL outage.
Preserve intent/response histories and queue identity, restart the same queue
storage, prove exporter recovery, stop/fence publishers and reconcile all original
ACKs and ambiguous requests. Check the full logical bodies/trace trees and count
physical duplicates, missing records and wrong values independently. Do not reset
or replay before recording the failed state. If explicit source replay is needed,
it is application repair with its own new witness and idempotency limitations.

Measure CPU utilization/core-seconds, average/peak memory, disk bytes/baseline/peak/
settled usage, available IOPS, network and native compression as well as admission,
visibility and query time. Report unavailable counters honestly. First queries
follow proven process restarts; host filesystem cache remains retained. Bounded
queries are latency diagnostics, not a sustained capacity winner.

## Pinned source decisions

- [Greptime OTLP traces](https://docs.greptime.com/user-guide/traces/read-write/):
  select `greptime_trace_v2` explicitly, with a separate table rather than mixing
  v1/v2 writes. The trace feature is documented experimental.
- [VictoriaTraces ingestion](https://docs.victoriametrics.com/victoriatraces/data-ingestion/)
  and [queries](https://docs.victoriametrics.com/victoriatraces/querying/): OTLP and
  native LogsQL/Jaeger APIs; Tempo compatibility is documented experimental.
- [Prometheus OTLP](https://prometheus.io/docs/guides/opentelemetry/): enable the
  receiver explicitly and keep label promotion/configuration visible.
- [Loki OTLP](https://grafana.com/docs/loki/latest/send-data/otel/): structured
  metadata and resource/index mapping differ from raw log-body fields.
- [Tempo architecture](https://grafana.com/docs/tempo/latest/introduction/architecture/):
  distinguish monolithic/local development from Kafka-based microservices mode.
- [Collector persistent storage](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.166.0/extension/storage/filestorage/README.md)
  and [export queues](https://github.com/open-telemetry/opentelemetry-collector/blob/v0.166.0/exporter/exporterhelper/README.md):
  fsync, persistence, retry, overflow and queue-level batching are separate knobs.

The common trace oracle excludes the Span sampling-flags bitfield: Greptime's
fixed trace table has no Span flags column. This is an explicit unsupported
projection, not a claim that every OTLP field survives. Keep source flags in the
fixture and report that boundary. Link flags remain unqualified in this initial
projection rather than being claimed universally unsupported. Business fields,
trace state, events and links
remain independently checked where the native API exposes them.

## Native trace indexing and sampled visibility

VictoriaTraces 0.13.0's native help declares a 20-second
`insert.indexFlushInterval` (30-second `search.latencyOffset`). Trace-ID lookup
requires a separate trace index. The live reader retains its selected acknowledged
record through delayed indexing; an age above 10 seconds is recorded as a
diagnostic, not substituted with a younger record or labeled lost. The controller
waits at most 120 seconds for initial/recovered old and recent visibility.
Observed delays, timeouts and final frozen reconciliation remain separate.
The [initial reader mistake and preserved partial case](FINDINGS.md) are explicit.
