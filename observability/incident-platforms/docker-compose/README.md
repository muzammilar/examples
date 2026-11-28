# Native incident-platform lab

## Quick start

Run from this directory. Requires local Docker Compose and Python 3.13; workload
Go, official OTLP protobufs, vet and tests build inside Docker. The default is the
separate OpenObserve **synchronous-WAL** profile. Select the native
[ClickStack component](deploy/clickstack/) with `export PLATFORM=clickstack` before
the lifecycle sequence. SigNoz and the new Grafana profile remain unrun. Run profiles serially; backend budget is 8 CPU / 12 GiB, with
another 1 CPU / 512 MiB for publisher/reader and additional measurement overhead.

1. `make docker-up` — validate/build and start the pinned native platform.
2. `make up` — publish a small control once, then independently verify its values.
3. `make test` — recheck that preserved control without reseeding it.
4. `make down` — reconcile every acknowledged case on this profile, then stop it.
5. `make docker-down` — remove only owned infrastructure after every existing
   profile has a current verified teardown receipt.

A failed or unfenced case blocks destructive cleanup. `make preserve-stop` stops
owned services while retaining containers, volumes and histories; it does **not**
issue a deletion receipt. The measured default-buffered failure remains on its
own volume, so this working checkout deliberately cannot complete `docker-down`.
A fresh checkout with only passing cases can use the normal sequence. Full
successful destruction/recreation of this new lab remains unrun. Automatic
approval review rejected exercising live `docker-down` even as an expected-refusal
test because a faulty guard could erase the failed dataset; five unit safety
tests cover the refusal path. See [lifecycle evidence](reports/LIFECYCLE.json).

## Bounded cases

- `make full`: 10,000 checkout requests, **1,000,000 metrics, 1,000,000 logs and
  30,000 spans** with continuous old/recent reads and exact fenced readback.
- `make ingress-crash`: same corpus with a held **10-second SIGKILL** of native
  ingress; source writers/readers continue. Preserve ACK/ambiguous journals,
  demonstrate exit 137 without OOM, restart the same container/storage and verify
  all histories. The 120-second gate requires old and new ACK visibility; one
  missing recent anchor can fail this gate even after newer requests succeed.
- `make verify-control CASE=<32-hex-id>`: independently reread a named preserved
  case on its original selected profile. It also supports a partial case after
  its actors have demonstrably stopped; investigate before freezing its history.
- `make test-production`: serial control, full healthy corpus and ingress crash.
- `make queries`: three fresh workers, three incident queries, ten warm repeats
  per query; requires healthy and recovery gates first.
- `make status`: owned service states.

Use `OPENOBSERVE_SERVICE=openobserve` on **every** command to select the retained
[buffered configuration](deploy/openobserve/default-buffered/). Its failed
recovery is separate from the [synchronous reference](deploy/openobserve/synchronous-wal/).
Never run both together: they share a lab DNS alias, not storage. The controller
rejects a wrong-volume case, competing variant and nonlocal Docker endpoint.

## Source, layout and evidence

`incident-workload/` is a tracked link to `../incident-workload/`; that source in
turn links the tracked deterministic corpus, acknowledgement reader and read-only
storage observer. `scripts/prepare.py` validates the link targets and materializes
hard links/copies into ignored `.build/incident-workload/` before Docker builds.
Neither generated context nor raw `results/` belongs in Git. No sibling Makefile,
container state, host Go toolchain or machine-local credential is required.

- `deploy/openobserve/`: shared pinned native image plus distinct WAL profiles.
- `deploy/openobserve-contract.json`: supported logical projection and limits.
- `scripts/`: lifecycle, independent case controller and resource/report tools.
- `tests/`: teardown safety checks; `python3 -m unittest discover -s tests -v`.
- `reports/`: compact curated good and bad evidence; raw bodies remain ignored.
- Parent [findings](../docs/FINDINGS.md) and [design](../docs/DESIGN.md).

The runtime network is internal, has no host-published ports and uses only static
lab credentials. Public OCI pulls use an empty example-owned Docker credential
configuration and explicit local socket. No AWS configuration/SDK, account or
real bucket is inspected or used. OpenObserve local disk is not its HA/object-
store architecture. All runs share one Docker VM and cannot prove independent
host/disk failure, power-loss safety, replication or production readiness.

Resource observation records CPU core-seconds/utilization, memory, network and
Docker disk bytes. Read-only volume sampling records logical/allocated bytes and
filesystem headroom. Source gzip bytes differ from native codec compression;
physical device IOPS/latency and native codec ratios remain unavailable where not
exposed. These paced correctness/fault runs are diagnostics, not capacity tests.

## Measured reference — 2026-11-28

Native ARM64 on an Apple M4 Pro host, Docker Desktop VM **11 CPU / 24.42 GiB**,
Linux **6.12.76-linuxkit**, Docker **29.5.3**, overlayfs over the local VM disk.
Backend image: OpenObserve OSS **1.2.0**, digest
`896361d533cac56ef346d940b1ddd6664e04d5f9606bd80b4a2e28069a33020e`.
One local SQLite/filesystem copy; profiles run sequentially. Source batches are
1,000 metric/log records or ten requests × three spans, gzip protobuf, no retry,
200 ms closed-loop delay after each batch. These are **single diagnostic runs**,
not repeated comparative throughput benchmarks.

| Healthy reference | Metric ACKs present | Log ACKs present | Span ACKs present |
|---|---:|---:|---:|
| Native buffered WAL | 1,000,000 / 1,000,000 | 1,000,000 / 1,000,000 | 30,000 / 30,000 |
| Synchronous WAL | 1,000,000 / 1,000,000 | 1,000,000 / 1,000,000 | 30,000 / 30,000 |

The synchronous healthy reference used **701,440,877 raw protobuf bytes** including
589,238,578 log bytes. The deterministic corpus has 100 tenants, tenant-0 owns
half the requests, 1% of log payloads are 4 KiB pseudorandom hex and other payloads
are repeated cart text; 250 incident requests generate 25,000 error logs, 500 error
spans and 1,250 elevated metric points. The fixture seed, exact timestamps, source
hashes and image references are in the [curated report](reports/bd209e76f1ee4eacb0cdfb16d8d1381c.json).

| Synchronous source admission | Samples | HTTP batch p50 | HTTP batch p99 | Raw/gzip source bytes |
|---|---:|---:|---:|---:|
| Metrics | 1,000 batches | 3.607 ms | 13.391 ms | 23.047× |
| Logs | 1,000 batches | 7.705 ms | 17.377 ms | 17.027× |
| Traces | 1,000 batches | 3.782 ms | 12.206 ms | 5.122× |

HTTP duration excludes encoding and journal fsync and does not measure per-record
latency, query visibility or physical durability. Timing uses Go monotonic elapsed
time; nanosecond representation does not establish nanosecond accuracy. Mean,
p95/p99.9 and raw byte totals remain in the report.

During the healthy synchronous **publication phase**, sampled backend CPU was
**59.077 core-seconds**, average **26.656% of one core**; publisher/reader added
**19.823 core-seconds**. Mean/peak aggregate memory was **1,279.114 / 1,936.730 MiB**.
Backend Docker block counters reported **0 read bytes** and **221,921,280 write
bytes** during the sampled window; this does not establish physical-cold
reads. Device IOPS, I/O latency and queue depth were unavailable through these
Docker counters. Volume allocation grew **8,818,688 → 143,015,936 bytes**, while
logical regular-file length was **2,691,239,488 → 2,707,834,540 bytes**, including
prior control/metadata and sparse/preallocated files. This is neither a codec
compression ratio nor a continuous transient-space peak. Native codec compression
remains unavailable; gzip above measures only source transport.

The buffered crash is a failed result: **30 / 17,310 span ACKs missing**; all
577,000 metric and 548,000 log ACKs survived. It is retained separately, not
qualified by the synchronous reference. See [findings](../docs/FINDINGS.md).

## Remaining limits and future tests

This level-3 native local path does not cover HA/operator scaling, quorum or disk
loss, partitions, rolling upgrades, independent restore, retention/TTL reclamation
or sustained compaction/pressure. No cluster topology is claimed from a single
monolith. Trace scope/state/type losses remain documented even when selected
logical values pass. Other-platform comparisons remain
pending; no winner follows from these one-off paced runs.

The synchronous crash case preserved **941,000 / 941,000 metric ACKs**,
**966,000 / 966,000 log ACKs** and **28,230 / 28,230 span ACKs**. The remaining
59,000 metric, 34,000 log and 1,770 span records had ambiguous/unacknowledged
requests and were absent at the verification cut; no replay occurred. Old/new
visibility recovered in **14.625 seconds**, including the ten-second outage.
This configuration's result does not erase the default-buffered omission.

A short [native incident-query screen](reports/OPENOBSERVE_QUERIES.json) after both
healthy/recovery gates returned the correct counts in all **99 queries**. Each
query had three fresh-worker first executions and 30 warm executions. SQL result
cache was disabled; engine and OS caches were retained. These are within-profile
diagnostics, **not physical-cold queries or a cross-platform winner**.

| Incident query | Correct result | Median fresh-worker first | Median warm |
|---|---:|---:|---:|
| Tenant-0 error logs | 25,000 logs | 21.178 ms | 9.292 ms |
| Tenant-0 error spans | 500 spans | 6.906 ms | 6.294 ms |
| Tenant-0 slow metric points | 1,250 points | 72.082 ms | 51.521 ms |

Timings cover HTTP request/response body, excluding source encoding and result
parsing. Per-trial observations, source hashes and sampled CPU/memory/disk/network
counters remain in the report; short-lived worker intervals have incomplete
utilization deltas. Dataset, stored fixtures and warmed server state remain the
same across these trials. Native storage compression and device I/O latency are
still unavailable. Alternating cross-platform trials remain pending.
