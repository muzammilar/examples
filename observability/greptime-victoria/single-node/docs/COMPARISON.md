# Focused single-node comparison — 2026-11-28

Victoria used less CPU/memory and returned the selected queries faster in this
synthetic workload. Greptime retained synchronous-WAL acknowledgements in the
separate crash tests; Victoria's buffered profiles lost acknowledged data until
external witness replay. **These are different persistence contracts**, so this
is not an equivalent-durability capacity ranking or a universal winner.

[COMPARISON.json](../reports/COMPARISON.json) contains every cell, source manifest,
fixture, timing sample, resource scope, storage snapshot and native codec gauge.
[Recovery results](RECOVERY.md) retain the failed crash case and its repair.

## Method and correctness

Same Apple M4 Pro, ARM64 Docker Desktop VM: 11 CPUs, 24.416 GiB, Linux 6.12.76,
Docker 29.5.3; physical disk class unverified. Native images remain pinned to
GreptimeDB 1.3.1, VictoriaMetrics 1.157.0 and VictoriaLogs 1.55.0. Backend budget
**4 CPU / 4 GiB**, split 2 CPU / 2 GiB per Victoria component. Identical aggregate
workload budget **1 CPU / 512 MiB**: two 0.5 CPU / 256 MiB writer/readers.
No other heavy lab or build overlapped the timed windows.

Three fresh-volume trials, alternating GT→Victoria, Victoria→GT, GT→Victoria.
Each starts with the same logical seed: one million metric samples plus one
million logs, including identical payload seed/timestamps. Each writer has its
own new run namespace and a two-million-record capacity. It then sends common
OTLP/gzip batches of 1,000, waiting 200 ms after each request. Each independent
reader queries old verified data and sampled recent ACKs. There is no collector
outside the budget. Native Remote Write/JSON results remain separately labeled
finite-corpus controls in the README; they were not mixed into these timed cells.

Discard 60 seconds of warmup; measure 300 seconds per actor, then fence actors
and verify every original ACK and ambiguous operation. The three trials retained
**9,971,000 Greptime ACKs** and **10,168,000 Victoria ACKs** across warmup plus
measurement, with **0 missing, 0 ambiguous and 0 wrong/unexpected records**. These
are different completed counts under the same paced closed-loop workload, not
identical final database sizes. All seed records were independently verified too.
Three generations completed verified teardown; retained journals were checked
before deleting only owned resources. No data reset concealed a failed run.

The corpus is intentionally synthetic: 10,000 series with 100 seed timestamps,
integer-valued gauges, hot-tenant skew, and logs with 99% repetitive 280-byte
payloads / 1% deterministic 4,096-byte hexadecimal payloads plus structured fields.
It does not represent histograms, cardinality churn, random floating values,
traces, cross-signal joins, production-fitted distributions or a saturated server.

## Paced ingestion

Rates are the median (min … max) of three measured windows. Latencies are medians
of the three **per-trial** percentiles, not pooled or averaged percentiles. Each
request contains 1,000 records; a request's milliseconds are not per-record
visibility latency. Approximately 1,368–1,425 measured requests per signal/trial
support these bounded p99 observations. Raw p99.9 values effectively describe
one or two extreme observations and do not establish a p99.9 tail.

| Engine / signal | Accepted records/s, median (min … max) | Request p50 | Request p99 |
|---|---:|---:|---:|
| Greptime metrics | 4,663.3 (4,660.0 … 4,670.0) | 6.427 ms | 9.791 ms |
| VictoriaMetrics | 4,750.0 (4,743.3 … 4,750.0) | 2.364 ms | 3.729 ms |
| Greptime logs | 4,563.3 (4,560.0 … 4,566.7) | 8.033 ms | 11.591 ms |
| VictoriaLogs | 4,663.3 (4,663.3 … 4,663.3) | 2.356 ms | 3.192 ms |

In this cell the median per-trial metric p99 was 2.63× lower on VictoriaMetrics;
log p99 was 3.63× lower on VictoriaLogs. The nearly equal rates mostly reflect
pacing; they are **not maximum throughput**. Request construction/encoding,
fsynced witness writes and the declared client quotas also contribute to the
completed rate. Encoding and HTTP time remain separate fields in the report.
HTTP acceptance is not equivalent to durable post-crash storage.

## Resource cost and storage

These numbers use the intersection of both actors' measured windows, about
299.8 seconds; one-second Docker counter samples exclude unsampled edges. Setup,
warmup, backend transitions and the fenced full-body oracle are excluded. Memory
is container usage including accounted cache, **not process RSS**. Whole-stack
figures include the workload; backend-only CPU is separately stated.

| Median across three trials | Greptime profile | Victoria profile |
|---|---:|---:|
| Backend CPU | 37.813 core-s | 20.028 core-s |
| Backend + workload CPU | 59.744 core-s | 44.334 core-s |
| Mean aggregate memory | 1.338 GiB | 0.379 GiB |
| Sampled peak aggregate memory | 2.066 GiB | 0.437 GiB |
| Reported backend disk writes | 228,990,976 B | 148,447,232 B |
| Reported backend disk reads | 0 B (range 0 … 266,240 B) | 1,089,536 B |
| Summed container network RX | 73,482,908 B | 74,784,157 B |
| Summed container network TX | 73,482,842 B | 74,784,157 B |

Whole-stack CPU was 1.35× lower and mean container memory 3.53× lower for Victoria
in these windows. Disk counters above cover the backend containers; workload
bind-mount I/O was not exposed by Docker, so these are not complete host I/O totals.
Zero recorded backend reads are compatible with cache hits and do not imply a
physical-disk-cold workload. Network sums include internal endpoints; do not add
RX and TX and call that unique application payload. Per-role utilization,
minimum/maximum, memory, resets, CPU caps and individual trials remain in JSON.

VictoriaLogs' native **on-disk small+big data-part** uncompressed/compressed ratio
was **21.381×**, **21.373×**, **21.381×** after the three trials. This excludes its
index, in-memory parts, WAL and filesystem allocation; it includes the million-log
seed and new logs. It is distinct from the roughly 11.31× OTLP wire gzip ratio.
No comparable Greptime/VictoriaMetrics native codec-byte pair was established;
those ratios remain unavailable rather than being inferred from protobuf bytes.

Before/after logical and allocated volume snapshots include WAL/index/metadata.
Different completed record counts and background maintenance prevent calling the
absolute final footprints an equal-size storage winner. Continuous peak disk
allocation, physical device IOPS/latency/queue depth and unexposed Docker IOPS are
unmeasured. These gaps limit the storage conclusion; they are not zero values.

## First query and warm queries

Three alternating trials, **one proven data-role restart before each query type**,
then one first request and 30 identical warm repetitions. All **558 executions**
returned the correct tenant groups/counts/sums against the independent corpus.
The fixture has one million metric points and one million logs. The first request
includes connection setup; host filesystem cache remains retained. This is
**engine-cold**, not physically cold storage. VictoriaMetrics uses `nocache=1`;
Greptime and VictoriaLogs retain their pinned native cache defaults.

| Query | Greptime first / warm median | Victoria first / warm median | Warm ratio |
|---|---:|---:|---:|
| Metric count by tenant | 137.845 ms / 101.963 ms | 25.080 ms / 13.927 ms | 7.32× |
| Metric sum by tenant | 128.595 ms / 106.457 ms | 23.534 ms / 12.133 ms | 8.77× |
| Log count by tenant | 10.496 ms / 5.976 ms | 1.476 ms / 0.970 ms | 6.16× |

“Warm median” is the median of three trial medians, with 30 warm samples per
trial. The report retains every sample and median/min/max/IQR. Variance matters:
Greptime's metric-count first queries ranged **128.961–430.327 ms**, and warm
trial medians **99.461–420.313 ms**. Physical layout/background maintenance was
not reset between these query-screen trials. This is a bounded latency observation,
not sustained query capacity, an established p99 tail or a universal database rank.

## Batch screen and commands

`make batch-screen`: 10,000 records per signal/cell, batches **1 / 100 / 1,000 /
10,000**, both engines, exact complete readback. Every cell passed. The screen
contains one finite-corpus attempt per cell; a 10,000-record batch has **one**
request, so its “p99” is just that request. It selects the 1,000-record paced
profile above; it does not establish an optimal batch size for production.

`make query-screen` runs the first/warm query trials. `make benchmark` runs the
three fresh-volume paced trials, **reconciling and recycling only this example's
owned state between trials**. All journals survive in ignored `results/`.
It requires an existing qualified common fixture from `make correctness`.
`make comparison-report` regenerates the curated report from completed evidence.
Use the README's five lifecycle commands for normal setup and verified teardown.

## What this does not qualify

Clustered/replicated comparisons, native-protocol sustained capacity, physical
power/disk/host loss, independent restore, partitions, upgrades, pressure/soak,
retention and cross-signal joins remain future work. The selected level-3
single-node work is complete; these follow-ups are not a reason to keep expanding
this one example while the shared three-signal and incident-stack labs are unvisited.
