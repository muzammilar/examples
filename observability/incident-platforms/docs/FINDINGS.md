# Initial native-path findings

OpenObserve OSS **1.2.0**, pinned native ARM64 image, one local SQLite/filesystem
node at **8 CPU / 12 GiB**. Workload builds/vets/tests in Docker. Healthy native readback and a failed buffered recovery were executed below.
No whole-platform benchmark winner is qualified.

The first setup failed before any workload publication: its root-password policy
requires uppercase/lowercase, a digit and a symbol. The initial container exited
1 without OOM. A corrected lab-only credential started on the **same volume**;
no data reset/reseed was needed. Read-only root filesystem, dropped capabilities
and explicit local Docker socket were verified before input.

The first driver control admitted **10,000 gauge points, 10,000 logs and 300 spans**.
Native schema/sample inspection is separate from full-body qualification. Input
case `0c4c2fd57dc54308b618415dbb06e22c` remains preserved with its original pending
status and source manifest; later independent readbacks qualified its supported
logical projection without altering that original status.

## Native trace projection is lossy

Observed native schemas/samples represent integer span/event attributes as strings.
Ordinary trace instrumentation scope and main-span trace state are absent. Events
and links are JSON strings; parent IDs, nanosecond start/end, microsecond duration,
status and selected correlation fields remain exposed. The pinned
[trace ingestion source](https://github.com/openobserve/openobserve/blob/v1.2.0/src/core/src/traces/mod.rs)
uses string-oriented attribute conversion, builds the stored Span projection and
hardcodes main-span flags to 1. A successful ingest/count therefore does **not**
qualify lossless OTLP trace replay/export. Alternate flags are unqualified, not
claimed measured corruption from this fixture whose flags are already 1.

This SQL projection differs from an API returning typed OTLP protobufs: declare
and verify its supported logical values while separately reporting lost types and
absent fields. Do not silently reconstruct absent scope/trace-state or describe
this native schema as a lossless copy. The [checked-in contract](../docker-compose/deploy/openobserve-contract.json)
records that boundary before the full corpus and query comparison.

The [log ingestion source](https://github.com/openobserve/openobserve/blob/v1.2.0/src/core/src/logs/otlp.rs)
retains log-attribute types and scope metadata; the
[metrics source](https://github.com/openobserve/openobserve/blob/v1.2.0/src/core/src/metrics/otlp.rs)
stores gauges as native points. The oracle compares exact body/value/correlation,
not counts alone. The native [search request](https://github.com/openobserve/openobserve/blob/v1.2.0/src/config/src/meta/search.rs)
exposes top-level `use_cache`; independent correctness queries set it false and
reject partial or result-cached responses. Physical file/page caching is a
separate question from stale query-result caching.

## Executed buffered reference and crash

The initial reader misinterpreted native link JSON casing/flattening and rejected
trace links. Its failed reports remain; correcting the reader against native
samples plus a wrong-ID regression check made the **same** small corpus pass
without replay/reset. This was an oracle defect, not a demonstrated backend link
failure. A full healthy case `b13c086902e3479b9403459c0372e702` then reconciled
**1,000,000 metric ACKs, 1,000,000 log ACKs and 30,000 span ACKs**, with zero
missing selected values or duplicate returned copies.

In crash case `7394da0773a04d169db860d23a517b68`, native ingress/storage/UI shared
one process. SIGKILL exited **137, not OOM**; both workload actors remained live
through a held ten-second outage. The 120-second old/new visibility gate failed.
Later ingestion succeeded, but its pending accepted trace anchor never appeared;
this is not evidence that the service was wholly unavailable for 120 seconds.

After fencing the partial run, the independent uncached audit found:

| Signal | ACK records | ACK records present | Missing | Ambiguous records absent at this cut |
|---|---:|---:|---:|---:|
| Metrics | 577,000 | 577,000 | 0 | 59,000 |
| Logs | 548,000 | 548,000 | 0 | 58,000 |
| Spans | 17,310 | 17,280 | **30** | 1,770 |

The missing spans belong to requests 90–99, one OTLP batch whose HTTP 200 completed
about **78 ms before the recorded process exit**. The older full healthy corpus
and small control still reconciled. No source replay, repair or reset occurred.
Physical WAL loss versus index/read-path omission is not fully isolated, and one
run does not establish a statistical loss bound.

The [pinned configuration source](https://github.com/openobserve/openobserve/blob/v1.2.0/src/config/src/config.rs)
defaults `ZO_WAL_FSYNC_DISABLED` to true. The pinned trace write path passes its
inverse to WAL writing. This is consistent with a buffered admission boundary,
but does not alone prove the observed omission's mechanism or a violated vendor
power-loss promise. It **fails this lab's ACK-survival gate**. Preserve its volume,
source, failed statuses and [curated evidence](../docker-compose/reports/7394da0773a04d169db860d23a517b68.json).
A separately named/owned synchronous-WAL reference sets that flag false; its
results cannot retroactively repair this original configuration.

## Separate synchronous-WAL reference

Service `openobserve-sync`, its own volume, explicitly
`ZO_WAL_FSYNC_DISABLED=false`; the buffered service was stopped with original data
retained. Effective environment, budgets, image and mounts were recorded before
input. Small control `394804bc0ac64c869a7f6eef819af923` and full healthy case
`bd209e76f1ee4eacb0cdfb16d8d1381c` passed the selected logical projection; the
latter reconciled **1,000,000 metrics, 1,000,000 logs and 30,000 spans**.

Crash case `f3b84be721604799933b2304d50bd49d` demonstrated exit 137 without OOM,
retained container/storage and continuously running publisher/reader. All-signal
old/new ACK visibility recovered **14.625 seconds after the kill**, including the
ten-second hold. Final uncached reconciliation found:

| Signal | ACK records | ACK records present | Missing | Ambiguous records absent at this cut |
|---|---:|---:|---:|---:|
| Metrics | 941,000 | 941,000 | 0 | 59,000 |
| Logs | 966,000 | 966,000 | 0 | 34,000 |
| Spans | 28,230 | 28,230 | 0 | 1,770 |

Older small/full fixtures also reconciled before and after the fault. No source
replay or reset occurred. This **single** bounded process-crash result supports
this configuration's measured ACK survival, not physical power-loss, permanent
disk loss, HA or a statistical durability guarantee. Native trace type/scope/state
limitations still apply. See [curated evidence](../docker-compose/reports/f3b84be721604799933b2304d50bd49d.json).
