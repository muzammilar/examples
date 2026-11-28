# Comparison design and evidence gates

## Common data and operations

The corpus is deterministic, synthetic and versioned. Default input is one million
metric points (10,000 series × 100 timestamps) and one million structured log
records, including tenant skew, known incident IDs, exact body values and mixed
payload compressibility. Timestamp precision is milliseconds for the common
subset; no untested nanosecond-preservation claim. Preserve timestamps, label sets,
values and log bodies in the oracle, not only totals. No delta/cumulative histogram
semantics are inferred from gauge tests.

Common ingestion uses official OTLP protobuf definitions over HTTP, same gzip
policy and batches 1/100/1,000/10,000. The bounded initial screen selects cells for
three alternating serial trials. Native Prometheus Remote Write and JSON log
paths are separate profiles. Never compare one engine's OTLP path with the other's
native path under the same label. Print deadlines, retries, pool/concurrency and
compression. Persist intent and response classifications; a transport timeout is
ambiguous, and HTTP success is admission rather than proof of durability.

Service/scope metadata uses version-specific label mapping: fixed service labels
may be `job` or `service.name`, and VictoriaMetrics exposes `scope.name` and maps an omitted scope version to
`scope.version="unknown"` (observed native series metadata). The
oracle checks those known fixed values and rejects unexpected business labels;
this does not claim both engines retain identical instrumentation metadata.

Decode OTLP response protobufs, including partial-success counts/messages. Empty
protobuf success bodies are valid. The pinned Victoria handlers reject OTLP JSON;
use binary protobuf on both systems. Request construction/encoding and network
round trip are separately timed; end-to-end includes both. Fixed-worker tests are
not maximum server capacity claims.

## Budgets and topology

Single-node backend budget: **4 CPU / 4 GiB** total. GreptimeDB gets that whole
budget; VictoriaMetrics and VictoriaLogs get **2 CPU / 2 GiB each**. The identical
workload gets 1 CPU / 512 MiB, separately reported and included in the total lab.
Run only one backend at a time, with the same VM, disk, network and workload
limits. Record effective cgroup limits and host co-tenancy; prepare/build work may
overlap functional cases, but comparative measurement waits for the CDC lab to
be fully stopped. Do not count an idle rival as part of a measured topology.

Use local persistent volumes and native WALs. GreptimeDB's standalone raft-engine
WAL has sync writes enabled. Verify and document Victoria's effective flush/
acknowledgement behavior before claiming equivalent acknowledged durability.
Native crash-and-reconcile is required; performance profiles with different
acknowledgement guarantees remain separate. Single-host restart tests do not
prove physical power-loss durability or independent-host availability.

## Queries and recovery

Verify every raw metric sample against its expected timestamp, series labels and
value; verify every log body, ID and timestamp with duplicate/missing detection.
Compare common queries: tenant/window counts, grouped sums, incident/error subset
and a selective record lookup. Native query syntax may differ but input and
answers must match. Disable result caches where supported and disclose exceptions.
Label first-query-after-process-restart **engine-cold**; Docker/host page cache is
retained. Warm repetitions use the same query and bounds. Physical OS-cache-cold
is unrun unless a separate owned testbed can establish it safely.

Keep a live writer and independent historical/recent readers during a backend
SIGKILL/retained-volume restart. Capture source emission, request ACK, query
visibility and post-restart existence separately. Stop/fence writers and compare
all successful and ambiguous requests after recovery; do not reseed missing data
before counting loss. Replay requires an explicitly verified duplicate policy:
logs may append duplicate records while metrics may replace identical timestamps.

Every phase reports CPU utilization/core-seconds, average/peak memory, whole-stack
allocated/logical disk, native compression where exposed, disk read/write bytes
and IOPS, network bytes, request/visibility/query timings and errors. Missing
native compression or physical device latency is unavailable, not zero. Compare
storage at equivalent quiescence/compaction points; include WAL/metadata and all
replicas in the cluster follow-up. Publish good and failed runs and bound the
conclusion to measured semantics.

## Pinned implementation research

- [GreptimeDB 1.3 OTLP mapping](https://docs.greptime.com/user-guide/ingest-data/for-observability/opentelemetry/):
  metrics/logs HTTP endpoints, partial success, new 1.3.1 log model and append-only
  logs. Extract only run/tenant tags; individual log IDs remain body fields so a
  million unique IDs do not silently create a million stream keys.
- [GreptimeDB 1.3.1 HTTP handler source](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/servers/src/http/otlp.rs):
  metrics can return a protobuf partial-success response on HTTP 200.
- [VictoriaMetrics OTLP](https://docs.victoriametrics.com/victoriametrics/integrations/opentelemetry/)
  and [1.157.0 source](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.157.0/app/vminsert/opentelemetry/request_handler.go):
  protobuf parsing, label normalization and insertion-buffer flush; this alone
  does not establish physical-disk persistence before acknowledgement.
- [VictoriaLogs OTLP](https://docs.victoriametrics.com/victorialogs/data-ingestion/opentelemetry/)
  and [1.55.0 source](https://github.com/VictoriaMetrics/VictoriaLogs/blob/v1.55.0/app/vlinsert/opentelemetry/opentelemetry.go):
  body/message mapping, resource stream fields and explicit stream-field override.
- [VictoriaMetrics deduplication](https://docs.victoriametrics.com/single-server-victoriametrics/):
  pin deduplication and do not confuse an identical-timestamp overwrite policy with
  general idempotency or append-only log deduplication.
