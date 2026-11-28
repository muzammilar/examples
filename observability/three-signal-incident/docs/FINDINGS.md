# Observed setup and small-control findings

2026-11-28; initial fixture `c9a9878f6883490aba4c24bf1322174a`: **10,000 gauges,
10,000 correlated logs and 300 spans**. Original failed statuses and histories
remain under ignored results; no replacement publication hid these failures.
Full healthy cases have run; collector recovery is in progress.

## Exact typed trace fidelity fails through Victoria's experimental Tempo API

GreptimeDB 1.3.1 and Prometheus/Loki/Tempo 3.16.0/3.7.11/3.1.1 passed the complete
small control, including original integer span/event attributes, parents, names,
status, times, resources, events, links and trace/log correlation. VictoriaMetrics
1.157.0 and VictoriaLogs 1.55.0 passed their metric and log readbacks.

VictoriaTraces 0.13.0's experimental Tempo-compatible protobuf query path changed
integer attributes into strings. Example: source `request.id = 1` and
`order.amount_cents = 101` returned as `"1"` and `"101"`; event attributes had the
same type change. Other selected structure and values matched. The strict
qualification **failed** and remains failed. This is an observed API-fidelity
boundary; it does not establish storage corruption or a fault in Tempo itself.
The [pinned compatibility marshaler](https://github.com/VictoriaMetrics/VictoriaTraces/blob/v0.13.0/lib/protoparser/opentelemetry/pb/tempo.go)
and [Jaeger model](https://github.com/VictoriaMetrics/VictoriaTraces/blob/v0.13.0/app/vtselect/traces/jaeger/model.go)
help explain why a string-oriented storage/query representation needs care when
projected back into typed OTLP. Its [Tempo compatibility documentation](https://docs.victoriametrics.com/victoriatraces/querying/)
labels that API experimental.

An explicitly separate `make diagnose-projection CASE=<id>` check verified all
**300 original spans**, all **10,000 logs** and all **10,000 metrics** without
replaying or changing any input. It permits only the precise numeric-attribute
string projection, records a projection count and reports exact type fidelity
false. It cannot hide changed IDs, parents, times, values or other structure;
a negative control verifies that distinction. This is a **secondary diagnostic**,
not a fix, a new passing exact-type result or proof that arbitrary OTLP types
survive. A `fidelity-mismatch` observation remains distinct from a transport error
or a value mismatch during the live cases. No trace performance winner is
qualified from a path with this failed exact-type result.

## Harness mistakes and corrected setup

- The first collector start failed before publication: enabling file-storage
  compaction left its default compaction directory under `/var` despite the data
  queue being mounted under `/data/queue`. The read-only container rejected it.
  Set both storage and compaction directories on the owned persistent queue
  volume; keep fsync enabled. The original config/log/exit are retained. See the
  [pinned storage configuration](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.166.0/extension/storage/filestorage/README.md).
- The CPU budget guard initially rejected Docker's NanoCPU serialization for
  5.5 CPUs: **5,500,000,256** rather than 5,500,000,000. Native `cpu.max` readback
  was **550000 / 100000**, exactly 5.5 CPUs. The guard now permits at most 1,024
  NanoCPUs of serialization rounding and retains the actual reported value; it
  does not permit changing the workload's declared CPU budget.
- The first trace oracle tried casting whole JSON2 structs to STRING and received
  `Unsupported CAST from Struct() to Utf8`. The acknowledged data was present.
  The corrected reader consumes native JSON objects/arrays and Greptime's event
  timestamp representation, then verifies the **same** original records. The
  failed report remains. This was an adapter assumption, not demonstrated data
  loss. Whole-object conversion uses JSON-specific functions where needed; leaf
  casts and JSON2 structs are different operations. See [JSON2 access](https://docs.greptime.com/user-guide/logs/json2/).

Grafana's services use upstream non-root users. A bounded initializer prepares
only an empty owned data volume for UID 10001 or 65534 and refuses to change the
owner of nonempty storage. It runs without network access, with only CHOWN added;
backend services keep their native users. Initialization is separate from the
steady-state resource budget.

The first bootstrap has resource observation; one corrective bootstrap retry ran
through its internal target without the outer sampler. Do not claim complete
startup-cost/peak coverage for that gap. The live corpus targets use the outer
observer, native process counters, data-volume snapshots and explicit scopes.

## Full corpus and a live-reader sampling error

The healthy full case `9bae05ff7ecb4206a821c9cfd82f925d` verified Greptime's
**1,000,000 metrics, 1,000,000 logs and 30,000 spans**, including exact typed
attributes, with zero missing acknowledged IDs. These are functional diagnostics,
not a capacity comparison.

Victoria's publisher was stopped after the controller's 120-second live-read
readiness deadline. The frozen partial journal independently reconciled **581,000
metrics, 558,000 logs and 17,460 spans** with zero missing acknowledged IDs and no
replay. All 17,460 spans require the previously documented numeric-to-string
projection; exact typed fidelity remains failed. Publication was incomplete and
this case remains failed even though the attempted acknowledged values survived.

The pinned VictoriaTraces binary's native `-help` declares a **20-second
`insert.indexFlushInterval`** and a 30-second `search.latencyOffset`. The reader
incorrectly abandoned a pending recent trace after 10 seconds, repeatedly choosing
a newer trace before its index could become searchable. Old traces remained
queryable throughout. The correction must retain each selected acknowledged trace
until it becomes visible, while continuing to record the exceeded 10-second
sampling budget. This is a harness sampling error, not demonstrated trace loss.
The [native indexing documentation](https://docs.victoriametrics.com/victoriatraces/)
and [pinned trace lookup source](https://github.com/VictoriaMetrics/VictoriaTraces/blob/v0.13.0/app/vtselect/traces/tempo/query.go)
explain the separate index required for trace-ID lookup. The original reader
history, failed status and partial readback remain preserved; a corrected run is
needed before claiming live recent-trace recovery.

### Grafana full readback

The independent Grafana readback passed **1,000,000 metrics, 1,000,000 logs and
30,000 spans**, with zero missing acknowledged IDs and exact selected typed
fields. Checking each signal took **0.757 s, 38.649 s and 125.509 s**, respectively;
these are whole-oracle times, not individual-query latency or a capacity ranking.
Tempo stayed running and was sampled near its **1.5 GiB** memory limit during
trace verification. No OOM or replica/failover claim follows from this local case.
See the [frozen initial report](../docker-compose/reports/HEALTHY_INITIAL.json).

During Greptime's live publication phase, sampled database CPU was **74.560 core-s**
and collector CPU **13.482 core-s**; the publisher used **17.534 core-s**, reader
**0.929 core-s**. Their observed windows differ slightly and exclude startup/exit
outside sampling. Database container disk writes were **220,667,904 B** and the
fsynced collector queue **1,491,673,088 B**. This includes background work and the
previous small fixture; it is not a pure per-record write-amplification estimate.
Mean/peak sampled aggregate memory was **1,620.334 / 2,828.500 MiB**, including
container cache. The post-readback database plus queue occupied **163,622,912 B**
allocated space; point-in-time allocation is not continuous peak or codec ratio.

The identical full Greptime/Grafana source gzip ratios were **23.048× metrics,
17.025× logs and 5.121× traces** relative to the serialized OTLP protobuf bytes.
This measures wire compression, not backend storage compression. Native codec
ratios/device IOPS and queue latency remain unavailable where not exported.
Resource reports identify their actual sampled phase and limitations.

The corrected Victoria live-reader case is
`44b30bb93c104a0f98c1fcb6b2ffd163`. Its first retained recent trace (request 59)
was observed at **20,178.1 ms** after collector ACK, with the numeric type projection
reported as `fidelity-mismatch`. Neither the native indexing interval nor backend
resource budget changed. This independently validates the sampling correction;
the original partial-publication failure remains failed. Full reconciliation of
this new case passed **1,000,000 metrics, 1,000,000 logs and 30,000 spans**, with
zero missing acknowledged IDs. All 30,000 spans require the numeric-attribute
projection; exact typed qualification and the command remain failed. Ten observed
recent trace samples became queryable at ACK ages **17,188.2–20,201.1 ms**. These
are sampled visibility observations, not a complete latency distribution. The
[separate frozen report](../docker-compose/reports/VICTORIA_HEALTHY.json) preserves
that distinction.
