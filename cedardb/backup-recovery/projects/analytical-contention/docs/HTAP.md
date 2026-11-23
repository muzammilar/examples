# Analytical contention under concurrent appends

This extends the same example-owned databases, rather than creating another
infrastructure copy. Follow the five-command lifecycle in [README.md](../../../README.md),
but run `make benchmark-htap` before cleanup. Defaults are **3 trials**, **60 s
warmup** and **300 s measurement** for each backend and traffic condition:

```sh
make benchmark-htap
# Short correctness qualification, not a performance result:
make benchmark-htap TRIALS=1 ANALYTICS_WARMUP_S=1 ANALYTICS_DURATION_S=3
```

The benchmark prints its owned result directory. After a complete sustained run,
publish it explicitly with
`make report-htap HTAP_RESULTS_DIR=results/htap/<run-id>` before changing the tested
source. This writes `HTAP_RESULTS.json` and the tables below. The publisher refuses
short, incomplete or failed cohorts, changed runtime source, mixed workload images,
incorrect resource budgets and summaries that disagree with raw query evidence.

Each cell cleanly restarts its database process with retained storage. Queries are repeated after the stated warmup; individual engine/OS cache-hit
ratios and page residency are not instrumented. This is not a cold-cache experiment. Trials
alternate backend and condition order. Both databases retain **4 CPUs / 6 GiB**;
the analytical client and each independent live writer/reader have **1 CPU / 512
MiB** each. The idle competing database remains on the same Docker VM. Whole-stack
costs and observer activity must be considered alongside engine-only measurements.

The analytical request groups exactly **1,000,000 baseline orders** (default
`ROWS`), **10,000 customers**, **256-byte payloads**, into **100 buckets**. It
returns each group key, count, sum of amounts and sum of payload lengths in key
order. The Go verifier independently computes every expected group from the seed
formula. A negative test moves an amount between two groups while preserving the
grand total; the verifier must reject it. Final full-body export uses the separate
Python oracle, including every historical live acknowledgement.

The concurrent condition keeps independent append and point-read actors running
at maximum **50 requests/s each** by default (`RATE`). The analytical subset uses
`id < ROWS`, so new tail inserts do not change query semantics. The point reader
also checks the newest committed row. This measures analytical/writer contention
with a fixed query input, rather than query freshness of a changing aggregation.
The live tail accumulates across cells; its exact acknowledged and ambiguous counts,
bodies and resulting storage growth remain in the evidence. It is not an
UPDATE/DELETE contention test, a transactional HTAP standard or writer saturation.

Raw query timings, 100-group validation outcomes, sampled resources, native process
and cgroup costs, compression/storage/I/O snapshots, external write witnesses and
full-body proofs go under ignored `results/`. The controller records source hashes,
image/environment metadata, actual measurement timestamps and bounded failure
outcomes. SQL/fetch/decode time is measured per query in **ms**; language validation
time is separate. Warmup queries are excluded. Report each trial and its sample
count, then median/range across trials; do not pool percentiles or present reciprocal
latency as measured throughput.

PID 1 RSS and high-water RSS are single-process observations. A live inventory
found two CedarDB processes, with most RSS in the second; PostgreSQL also has
separate backend/worker processes. Use the database cgroup for whole-database
charged memory. Summing process RSS would double-count shared pages. Native
process/container peaks include warmup and final verification, while sampled
measurement-window memory is reported separately.

Timing audit: one completed CedarDB cell logged **299.9970915 s** between UTC
measurement markers, a **2.9085 ms** shortfall, while its native monotonic main
interval was **360.049120289 s**. Phase deadlines use `time.Since`, so the verified
driver completes 60 s warmup and 300 s measurement independently of wall-clock
adjustments. The original strict UTC audit failure is preserved. Publication
allows at most **10 ms** of UTC shortfall only with matching declared phases,
normal completion, the verified loop source/image and sufficient monotonic main
time; larger discrepancies still fail. Rates/resource windows retain the actual
UTC markers. Exact per-phase monotonic elapsed was not logged in this cohort;
the subsequent instrumentation revision records it directly. See
[Go's monotonic clock semantics](https://pkg.go.dev/time#hdr-Monotonic_Clocks).

## Current instrumentation

Each new phase-end record contains native monotonic `elapsed_ns`. New query-rate
denominators and duration gates use that counter directly; even a one-nanosecond
shortfall fails. UTC markers remain for history/resource correlation. Legacy logs
remain readable with their explicit timing audit. The historical million-row
comparison in this document was not rerun or relabeled with the new counters; use its
tested source hashes (commit `4b2984d`) when reproducing that publication.

The revised driver passed **four short native cells** with **10,000 seeded rows
per backend**, 1 s warmup and 3 s measurement, complete body/ACK reconciliation,
native duplicate/missing-key controls and **17 controller/report tests**. This
qualifies instrumentation/correctness, not sustained performance. Host peak RSS
converts Darwin bytes and Linux KiB into bytes. The Darwin host path ran natively;
Linux unit conversion is unit-tested, with Linux-host integration still unrun.
See [METRICS_QUALIFICATION.json](../../../reports/METRICS_QUALIFICATION.json).

The reviewed Go driver passed **four short native qualification cells** (both
engines, alone/concurrent): **111 / 33 / 81 / 34** analytical requests respectively,
with every group checked. Final full-body verification retained **589 CedarDB** and
**596 PostgreSQL** cumulative live ACKs, including the earlier qualification's
writes; **zero ACKs were missing**. See
[HTAP_QUALIFICATION.json](../reports/HTAP_QUALIFICATION.json) for exact tested hashes and costs.
These short counts establish correctness, not comparative performance. The
three-trial sustained cohort passed; results follow below. The earlier
recovery cohort in `VERIFIED_RESULTS.json` records its own tested source hashes;
these new source changes do not retroactively qualify that cohort's new features.

References: [CedarDB transactions](https://staging.cedardb.com/docs/references/transactions/)
and [startup resource settings](https://cedardb.com/docs/references/configuration/).
CedarDB uses snapshot isolation; this append-only workload does not check arbitrary
transactional anomalies. PostgreSQL uses a fresh statement snapshot at READ COMMITTED;
immutable analytical input makes that difference irrelevant for the selected query.

Final retained-data checks verified **2,109,581 stored bodies**, all **109,580
historical ACKs**, native duplicate/missing-key controls and **14 controller/report
tests**. CedarDB also contained **one applied but unacknowledged intent**; it is
not reclassified as an ACK. No acknowledged data was missing. Scoped teardown
removed all example containers/volumes and its built images, preserving unrelated
resources and raw evidence. See [HTAP_FINAL_CHECKS.json](../reports/HTAP_FINAL_CHECKS.json).

<!-- htap-results -->

## Sustained repeated results

**2026-11-21**: 12 complete cells, 3 fresh database-process trials per backend/traffic condition, 60 s warmup and at least 300 s measured per cell. All analytical groups and complete seeded/acknowledged bodies passed verification. Input is fixed and repeatedly queried after warmup; writes are append-only; no general HTAP or cold-cache winner is established.

| Backend | Traffic | Measured queries/s median (min … max) | Rate IQR | Median trial p50 ms |
|---|---|---|---|---|
| cedar | alone | 36.92 (36.71 … 36.94) | 0.12 | 26.943 |
| cedar | live appends + point reads | 30.56 (30.24 … 32.42) | 1.09 | 33.511 |
| postgres | alone | 11.07 (11.03 … 11.12) | 0.05 | 90.936 |
| postgres | live appends + point reads | 11.34 (11.25 … 11.46) | 0.11 | 89.481 |

| Traffic | Higher median query throughput | Median rate ratio | Lower observed DB CPU/query | Lower sampled DB mean memory |
|---|---|---|---|---|
| alone | cedar | 3.33× | cedar | postgres |
| concurrent | cedar | 2.69× | cedar | postgres |

These are descriptive resource/throughput comparisons for one fixed aggregate, not significance tests or a universal ranking. Concurrent query-rate changes relative to each engine's alone case: cedar -17.2%; postgres +2.4%. CPU/query uses observed sampled intervals, excludes initial/final partial intervals and includes database-side observer costs; it is not a complete service-demand measurement.

| Backend | Trial | Concurrent | Samples | p50 / p99 ms | Observed DB CPU core-s | Sampled DB memory mean/max MiB | DB allocated MiB |
|---|---|---|---|---|---|---|---|
| cedar | 1 | False | 11014 | 27.060 / 28.988 | 1144.58 | 546.6 / 551.3 | 1602.7 |
| postgres | 1 | False | 3309 | 91.013 / 114.915 | 1196.14 | 388.0 / 391.1 | 696.9 |
| cedar | 1 | True | 9073 | 33.675 / 40.461 | 1173.56 | 581.7 / 587.4 | 1602.7 |
| postgres | 1 | True | 3439 | 88.598 / 102.097 | 1194.96 | 420.5 / 428.1 | 702.8 |
| postgres | 2 | True | 3376 | 89.740 / 105.589 | 1194.79 | 426.0 / 433.4 | 692.7 |
| cedar | 2 | True | 9725 | 31.445 / 36.510 | 1175.68 | 617.5 / 630.4 | 1608.0 |
| postgres | 2 | False | 3322 | 90.936 / 109.046 | 1198.19 | 399.0 / 402.3 | 692.7 |
| cedar | 2 | False | 11078 | 26.933 / 28.763 | 1143.89 | 580.3 / 585.4 | 1615.4 |
| cedar | 3 | False | 11084 | 26.943 / 28.459 | 1144.28 | 535.5 / 542.1 | 1615.4 |
| postgres | 3 | False | 3337 | 90.718 / 105.825 | 1198.39 | 399.1 / 402.0 | 692.7 |
| cedar | 3 | True | 9168 | 33.511 / 37.852 | 1178.45 | 613.2 / 620.1 | 1615.4 |
| postgres | 3 | True | 3403 | 89.481 / 101.096 | 1198.68 | 431.7 / 439.0 | 698.6 |

Per-trial query latency, actual throughput/window, independent read/write OK/error/ambiguous rates and latency, CPU utilization/core-seconds, memory, compression and disk/I/O bytes/rates are retained in [HTAP_RESULTS.json](../reports/HTAP_RESULTS.json). Raw logs/witnesses remain ignored. Native cgroup memory peaks include warmup and final verification; sampled measurement-window peaks are separate. Native container I/O counters may omit Docker Desktop cache/VM traffic. Unavailable IOPS/latency/queue metrics are not zeros.
<!-- /htap-results -->
