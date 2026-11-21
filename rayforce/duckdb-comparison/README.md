# RayforceDB versus DuckDB: embedded analytics in three languages

Compare the same pinned engines through **Python, C++ and Rust** over 2,000,000
seeded synthetic trades and 10,000 customers. Monitor CPU, memory, block I/O,
input storage and latency;
check every query result against an independent Python integer oracle.

## Quick start

1. `make docker-up` — verify source downloads and build both engines and all three drivers.
2. `make up` — generate or verify the example-owned fixture and reference results.
3. `make test` — run six full-data correctness cases and 18 negative controls.
4. `make down` — verify no application containers remain; preserve evidence and inputs.
5. `make docker-down` — remove this example's infrastructure and built image.

Docker Compose, a local Unix Docker API, cgroup v2 and host Python 3.11+ are
required for the measured harness. No host C++/Rust compiler or
Python packages are needed. There is no resident database server: every test starts
its own embedded database inside an isolated one-shot Compose service. No ports,
network access, external database state or sibling example outputs are needed at
runtime. Initial source/toolchain downloads need network access.

Run measurements before cleanup:

```sh
make benchmark                         # three trials, one CPU/thread
make THREADS=4 benchmark               # three trials, four CPUs/threads
make REPEATS=50 TRIALS=5 benchmark      # longer run
```

`ROWS=2000000` defaults to two million; `REPEATS=15`, `TRIALS=3`,
`THREADS=1`, `CPUS=$(THREADS)`. Threads and CPU caps support 1–4; each process has a
**4 GiB** container ceiling. Changing `ROWS` requires archiving the old `results/`
directory first. Failed runs remain evidence; no automatic data reset or retry.
An example-local lock prevents concurrent controllers. Other examples must be
stopped independently before performance measurements.

## Setup and language boundary

| Component | Pin / method |
|---|---|
| RayforceDB | source commit `38fd4be3560d63a5514c1e6883685fe496d45e1a`, 2026-11-16; **development revision after v3.0.2**, not the v3.0.2 release |
| DuckDB | official native Linux shared library **2.0.0** |
| Native adapter and C++ driver | C17 / C++20, GCC/G++ 12.2.0, CMake Release |
| Python | 3.12.12, standard-library `ctypes` |
| Rust | 1.91.1, Cargo release, `libc=0.2.177`, lockfile, localized C FFI |
| Resources | one process at a time, 1 or 4 CPUs/engine threads, 4 GiB memory, no network |
| Input | seed 20261117, six I64 trade columns, customer/region/factor dimension; all fields non-null |

All languages call the same small native adapter through its C ABI. It executes
SQL for DuckDB or Rayfall for Rayforce, converts the numeric result table to a
canonical row sequence and releases native results. This deliberately controls
engine versions, query semantics, output conversion and threading across languages.
It **does not benchmark the official Python/Rust DataFrame interfaces**, language
execution speed generally, RPC, or a pure-Rust database. C++ calls the adapter
directly, Python uses `ctypes`, and Rust uses an owned FFI result wrapper. The
engine implementation remains native in every case. Go is not included.

The current official Rayforce Rust binding's build script vendors core `0aa2d6a`;
using it alongside a different core for Python/C++ would confound this comparison.
The adapter is limited to the benchmark's non-null integer tables, not a new
client library. The source revision is identical across all drivers.

Rayforce uses upstream optimized library flags and explicit `armv8-a+simd` on
ARM64 (`x86-64-v3` on amd64). Debian Clang 14 rejects ARM `-march=native` and an
ALP floating-point build option; **GCC builds with the original strict warning
and floating-point settings**. No upstream source is patched. The recorded ARM64
run does not qualify the amd64 path. DuckDB uses its upstream release binary;
this is not a same-compiler engine-build comparison. Checksummed upstream
archives are pinned in `sources.json`; local downloads/build inputs are ignored.

DuckDB's internal buffer limit is 3 GB; Rayforce retains its native cgroup-aware
spill watermark. Both have the same 4 GiB **whole-process/container** limit; the
measured peaks are well below either internal threshold. This test does not
exercise spilling, out-of-memory recovery or larger-than-memory datasets.

## Dataset size and shape

Measured directly from the fixture used by both engines and all three languages:

| Property | Value |
|---|---|
| Trade rows / unique trade IDs | 2,000,000 / 2,000,000 |
| Trade columns | six I64 columns: `id`, `customer`, `symbol`, `day`, `qty`, `price` |
| Customer rows / distinct referenced customers | 10,000 / 10,000 |
| Customer columns | three I64 columns: `customer`, `region`, `factor` |
| Distinct symbols | 256, IDs 0–255 |
| Distinct day buckets | 30, indices 0–29; synthetic indices, not dated event timestamps |
| Distinct customer regions | 16 |
| Trade CSV | 52,801,261 bytes (**50.36 MiB**) |
| Customer CSV | 92,663 bytes (**0.08837 MiB**) |
| Combined CSV input | 52,893,924 bytes (**50.44 MiB**) |
| Generator seed | 20261117 |

Sizes include CSV headers and LF record delimiters, exclude the oracle, plans and
result logs, and are **input-file sizes**, not database storage or RAM footprints.
Artifact SHA-256 hashes are retained in the fixture manifest and copied into
each run's setup evidence.

## Workload and correctness

| Query | Exact result compared |
|---|---|
| totals | trade count, quantity sum, integer notional sum |
| filtered | the same three aggregates where price >= 9000 cents and quantity <= 5 |
| symbols | count/notional per symbol, all 256 groups in key order |
| customers | count/notional per customer, all 10,000 groups in key order |
| join | trades joined to 10,000 customers, count/weighted notional per region, all 16 groups |

An independent streaming generator computes reference results while writing CSV.
Every first, warmup and timed query must return the exact ordered reference rows;
a mismatch exits nonzero. Both engines use signed integer input and all totals
fit I64; DuckDB's wider SUM result is converted to I64 within that proven range.
The 18 negative controls cover an incorrect expected answer, an engine query
error and a null output for every engine/language combination. Rust formatting
and Clippy with warnings denied run during the image build; native drivers build
with warnings treated as errors. The final `make test` passed **180 full-data
query checks and all 18 negative controls** at one CPU. Both benchmark profiles
also validated every query at their actual thread count. **Ten controller tests**
cover partial failure evidence, unknown runtime/image state, CPU timing windows,
sampled memory/CPU summaries and kernel I/O accounting.

## Measurement and limits

Rerun **2026-11-21** on the original 2026-11-17 fixture, **Apple M4 Pro**, native **aarch64** Docker VM,
**11 CPUs / 24.42 GiB**. No other database containers were running. Three fresh
process trials per engine/language/profile; pair order reverses on alternate
trials. Each trial bulk-loads the same CSV, executes each of five queries once,
performs three warmup rounds, then measures **15 warm rounds**. Queries are submitted as SQL/Rayfall source strings on every call, without
prepared statements. Each measured query includes engine parsing/planning/execution and complete native integer
result conversion. Language-side expected-value comparison, printing and plan
parsing sit outside the query timer. Per-query process CPU sums user/system time
across threads; cgroup CPU/peak memory are read before container exit. Native
Docker API observations run approximately every **100 ms plus inspection/API
overhead**, retaining charged, anonymous, file-cache and kernel memory counters
where available. Sample means/maxima and CPU peaks are separate from **native
cgroup `memory.peak`, process peak RSS and cumulative CPU totals**. The collector
runs outside the engine container. Host controller CPU/peak RSS and child CLI CPU
are recorded separately; controller average RSS and Docker daemon/VM resource
costs are unavailable in this harness. This shared developer workstation does not
establish dedicated-machine capacity.

Kernel `io.stat` records read/write bytes and I/O counts per device, with rates
over each actual container lifetime. This is block accounting, not logical engine
read traffic; cached input and Docker Desktop file sharing can leave counters at
zero despite host disk activity. I/O latency and queue depth are unavailable.
The same CSV occupies **52,893,924 logical / 52,895,744 host-allocated bytes**.
Both engines open in memory with read-only input/root filesystems; `/tmp` is a
**512 MiB tmpfs** charged to memory. No native database files persist, so a native
storage-compression ratio is **not applicable**, rather than an inferred 1:1 ratio.
Persistence, compression and disk-storage comparisons still need a separate path.

The full lifecycle and the **36-process** benchmark matrix completed on the rerun.
Cleanup left no owned containers or workload image tag; **nine unrelated
containers and ten volumes** retained their identities/states. Inputs, oracle,
native results and failed/diagnostic evidence remain under ignored `results/`.

Warm latency tables report the **median of three trial p50s**, in milliseconds.
Raw per-query CPU/latencies, p99s (only 15 samples per trial, close to the maximum),
first-query observations, source fingerprints, exact image ID, cgroup memory
counters, runtime inspection and resource samples live in ignored `results/runs/`.
[Committed benchmark results](BENCHMARK_RESULTS.json) retain per-trial resources,
query summaries, fixture/source hashes, environment and instrumentation limits.
Three trials are a local comparison, not confidence intervals or a universal
leaderboard. Both engines are in-memory for these runs; the CSV input is cache
warm. No global cache eviction is performed on the shared Docker VM.

Bulk CSV load is the only ingestion approach currently measured. Single-row
versus batched mutation, prepared-query APIs, genuinely cold persisted queries,
persistence/WAL recovery, process/disk faults, nullable/string/time-series/as-of
queries and larger-than-memory operation remain **not run**. There is no native
cluster in this example and no production-readiness claim. The synthetic uniform
keys omit real market-data skew and strings. The output conversion cost matters,
particularly for the 10,000-row customer result. Small cross-language differences
must not be presented as proof that one language makes the native engine faster.

## Outcome

DuckDB won the total scan, selective aggregate and dimension join in all three
languages at both CPU budgets. Rayforce won the 256-group and 10,000-group
aggregations, and loaded the numeric CSV faster. DuckDB used less process peak
RSS, cgroup peak memory and whole-run CPU for this five-query mix. There is no
single winner for all workloads; use the per-query measurements below.

## Directory structure

```text
analytics-workload/
  native/{bridge.c,bridge.h}     # bounded numeric result adapter, engine ownership
  cpp/main.cpp                  # C++ workload, RAII, per-query CPU/latency
  python/main.py                # Python workload, ctypes, per-query CPU/latency
  rust/{Cargo.toml,Cargo.lock,build.rs,src/main.rs}
  CMakeLists.txt
docker/Dockerfile
{docker-compose.yml,Makefile}
{prepare.py,experiment.py,test_controller.py,write_results.py,sources.json}
BENCHMARK_RESULTS.json          # committed compact measured results
.build/                         # ignored, verified source archives/build context
results/{data,plans,runs}/       # ignored inputs, oracle, raw evidence
```

`prepare.py` downloads verified upstream sources into the local `.build/` context;
there are no source symlinks or dependencies on another example's generated files.
Cleanup removes only the named Compose project and `rayforce-duckdb-analytics:dev`;
all inputs and evidence remain local. Re-running `up` verifies hashes rather than
silently overwriting inputs. No Docker volumes are created.

## Inspected upstream references

- [Rayforce pinned runtime and materializing evaluator](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/lang/eval.c), [C ABI and ownership](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/include/rayforce.h).
- [Rayforce CSV typing](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/docs/docs/namespaces/csv.md), [select/aggregation](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/docs/docs/queries/select.md), [joins](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/docs/docs/queries/joins.md).
- [Rayforce explicit total-worker initialization](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/core/pool.c), [runtime/cgroup accounting](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/core/runtime.c).
- [Official Rust binding's vendored core pin](https://github.com/RayforceDB/rayforce-rs/blob/8dc8792288bd6b6742d764c20302c05c844487df/rayforce-sys/build.rs).
- [DuckDB 2.0.0 C query execution](https://github.com/duckdb/duckdb/blob/v2.0.0/src/main/capi/v1/duckdb-c.cpp), [typed result extraction](https://github.com/duckdb/duckdb/blob/v2.0.0/src/main/capi/v1/value-c.cpp), [release binaries](https://github.com/duckdb/duckdb/releases/tag/v2.0.0).
- [Kernel cgroup v2 counters](https://docs.kernel.org/admin-guide/cgroup-v2.html), [Linux 6.12 block accounting implementation](https://github.com/torvalds/linux/blob/v6.12/block/blk-cgroup.c). Zero-I/O devices can have no counter fields; missing accounting is reported separately.

<!-- measured-results -->

### 1 CPU / 1 engine thread

| Query | Python Ray ms | Python Duck ms | C++ Ray ms | C++ Duck ms | Rust Ray ms | Rust Duck ms |
| --- | --- | --- | --- | --- | --- | --- |
| totals | 23.072 | 2.075 | 23.253 | 2.057 | 22.901 | 2.094 |
| filtered | 10.994 | 1.924 | 9.109 | 1.915 | 9.031 | 1.952 |
| symbols | 3.269 | 10.211 | 3.296 | 10.034 | 3.260 | 10.207 |
| customers | 5.496 | 15.792 | 5.540 | 17.382 | 5.525 | 16.919 |
| join | 68.876 | 13.156 | 69.952 | 13.265 | 69.133 | 13.219 |

### 4 CPU / 4 engine threads

| Query | Python Ray ms | Python Duck ms | C++ Ray ms | C++ Duck ms | Rust Ray ms | Rust Duck ms |
| --- | --- | --- | --- | --- | --- | --- |
| totals | 6.664 | 1.200 | 6.710 | 1.193 | 6.573 | 1.170 |
| filtered | 3.652 | 1.235 | 3.125 | 1.088 | 3.086 | 1.135 |
| symbols | 1.232 | 3.741 | 1.251 | 3.809 | 1.257 | 3.936 |
| customers | 3.822 | 8.431 | 3.635 | 8.345 | 3.655 | 8.571 |
| join | 21.059 | 6.175 | 21.210 | 6.218 | 21.461 | 6.312 |

### Resource costs

Medians of three process runs. CSV timing covers the 2-million-row trade import; M rows/s uses that duration. Container CPU includes both imports, all 95 checked queries, runtime startup/shutdown and telemetry commands. RSS is the process high-water mark; cgroup peak also includes charged file cache/kernel memory and wrapper processes. These are whole-run peaks, not per-query retained heap sizes.

| CPUs | Language | Engine | CSV ms | M rows/s | CPU core-s | Peak RSS MiB | Cgroup peak MiB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | python | rayforce | 132.61 | 15.08 | 2.347 | 368.7 | 413.8 |
| 1 | python | duckdb | 281.45 | 7.11 | 1.217 | 239.7 | 248.5 |
| 1 | cpp | rayforce | 138.72 | 14.42 | 2.309 | 361.9 | 409.7 |
| 1 | cpp | duckdb | 290.48 | 6.89 | 1.236 | 230.9 | 242.0 |
| 1 | rust | rayforce | 138.40 | 14.45 | 2.280 | 362.2 | 411.1 |
| 1 | rust | duckdb | 293.06 | 6.82 | 1.223 | 227.2 | 237.5 |
| 4 | python | rayforce | 46.90 | 42.65 | 2.682 | 387.3 | 433.4 |
| 4 | python | duckdb | 102.06 | 19.60 | 1.557 | 260.0 | 267.5 |
| 4 | cpp | rayforce | 49.89 | 40.09 | 2.649 | 380.8 | 429.4 |
| 4 | cpp | duckdb | 103.29 | 19.36 | 1.517 | 247.6 | 259.0 |
| 4 | rust | rayforce | 47.38 | 42.21 | 2.626 | 377.7 | 427.4 |
| 4 | rust | duckdb | 108.15 | 18.49 | 1.566 | 252.0 | 262.5 |

### CPU utilization, sampled memory and block I/O

Medians of three process trials. One CPU core is 100%; four cores can approach 400%. CPU average uses cumulative cgroup CPU divided by the actual container lifetime, not Docker polling duration. CPU peaks and charged-memory means/maxima come from approximately 100 ms API observations; means are arithmetic sample means. Native cgroup peak and process RSS above remain separate counters. Raw per-device io.stat is retained. Read/write IOPS divide exported operation counts by container lifetime; these are not logical CSV or engine reads.

| CPUs | Language | Engine | CPU avg % | Sample CPU peak % | Mean charged MiB | Sample max MiB | Read bytes | Write bytes | Read IOPS | Write IOPS |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | python | rayforce | 97.6 | 100.3 | 386.3 | 413.8 | 0 | 0 | 0.00 | 0.00 |
| 1 | python | duckdb | 95.9 | 100.0 | 213.3 | 248.5 | 0 | 0 | 0.00 | 0.00 |
| 1 | cpp | rayforce | 97.4 | 100.2 | 385.0 | 409.7 | 0 | 0 | 0.00 | 0.00 |
| 1 | cpp | duckdb | 96.0 | 100.1 | 201.8 | 241.1 | 0 | 0 | 0.00 | 0.00 |
| 1 | rust | rayforce | 97.1 | 100.2 | 384.9 | 411.1 | 0 | 0 | 0.00 | 0.00 |
| 1 | rust | duckdb | 95.5 | 100.0 | 204.8 | 237.5 | 0 | 0 | 0.00 | 0.00 |
| 4 | python | rayforce | 324.7 | 359.9 | 395.6 | 433.1 | 0 | 0 | 0.00 | 0.00 |
| 4 | python | duckdb | 257.0 | 300.0 | 211.9 | 266.2 | 0 | 0 | 0.00 | 0.00 |
| 4 | cpp | rayforce | 330.6 | 359.9 | 393.5 | 429.1 | 0 | 0 | 0.00 | 0.00 |
| 4 | cpp | duckdb | 263.5 | 308.6 | 206.3 | 257.8 | 0 | 0 | 0.00 | 0.00 |
| 4 | rust | rayforce | 333.0 | 360.4 | 391.8 | 427.1 | 0 | 0 | 0.00 | 0.00 |
| 4 | rust | duckdb | 265.6 | 307.0 | 214.7 | 261.5 | 0 | 0 | 0.00 | 0.00 |

Zero exported block counters describe this cached, in-memory run; Docker Desktop file sharing can do host I/O outside the container cgroup. They do not establish zero physical disk activity or disk-performance parity. No native database files are persisted, so native storage compression is not applicable to this profile. Input CSV logical/host-allocated bytes are recorded in setup.json and BENCHMARK_RESULTS.json; /tmp is memory-backed. Device I/O latency and queue depth were not exposed.

### Successful-query CPU time

C++ representative: median of each trial’s mean process CPU milliseconds per warm query, including native result conversion. CPU time sums all engine threads and may exceed wall latency. Python/Rust have the same raw counters in their result files.

| CPUs | Query | Ray CPU ms | Duck CPU ms |
| --- | --- | --- | --- |
| 1 | totals | 23.229 | 2.095 |
| 1 | filtered | 9.241 | 1.967 |
| 1 | symbols | 3.329 | 10.113 |
| 1 | customers | 5.667 | 17.548 |
| 1 | join | 69.836 | 13.960 |
| 4 | totals | 26.401 | 3.129 |
| 4 | filtered | 11.112 | 2.950 |
| 4 | symbols | 4.327 | 12.562 |
| 4 | customers | 9.117 | 24.666 |
| 4 | join | 78.730 | 19.385 |

### Trial variability

C++ per-trial p50 milliseconds: median (minimum … maximum), plus inclusive IQR across three fresh processes. These are trial-median spreads, not pooled request tails or confidence intervals. Python/Rust trial values and spreads are retained in BENCHMARK_RESULTS.json.

| CPUs | Query | Ray median (min … max) ms | Ray IQR ms | Duck median (min … max) ms | Duck IQR ms |
| --- | --- | --- | --- | --- | --- |
| 1 | totals | 23.253 (22.666 … 23.261) | 0.298 | 2.057 (2.019 … 2.072) | 0.026 |
| 1 | filtered | 9.109 (8.977 … 9.157) | 0.090 | 1.915 (1.913 … 1.938) | 0.012 |
| 1 | symbols | 3.296 (3.221 … 3.385) | 0.082 | 10.034 (9.985 … 10.374) | 0.195 |
| 1 | customers | 5.540 (5.482 … 5.556) | 0.037 | 17.382 (17.273 … 18.190) | 0.458 |
| 1 | join | 69.952 (68.778 … 70.254) | 0.738 | 13.265 (13.121 … 13.312) | 0.095 |
| 4 | totals | 6.710 (6.544 … 6.766) | 0.111 | 1.193 (1.131 … 1.200) | 0.035 |
| 4 | filtered | 3.125 (3.083 … 3.167) | 0.042 | 1.088 (1.086 … 1.122) | 0.018 |
| 4 | symbols | 1.251 (1.213 … 1.298) | 0.042 | 3.809 (3.706 … 4.010) | 0.152 |
| 4 | customers | 3.635 (3.452 … 4.270) | 0.409 | 8.345 (8.095 … 8.509) | 0.207 |
| 4 | join | 21.210 (21.161 … 21.426) | 0.133 | 6.218 (6.100 … 6.264) | 0.082 |

### First query after fresh import

C++ medians across three fresh processes. Each query appears once in the fixed order shown, after CSV loading. This is **not an OS-cold-cache measurement**: loading and earlier queries have already touched pages.

| CPUs | Query | Ray first ms | Duck first ms |
| --- | --- | --- | --- |
| 1 | totals | 26.632 | 4.220 |
| 1 | filtered | 10.990 | 2.981 |
| 1 | symbols | 3.381 | 10.102 |
| 1 | customers | 6.080 | 27.312 |
| 1 | join | 77.540 | 17.017 |
| 4 | totals | 7.578 | 2.662 |
| 4 | filtered | 3.096 | 1.320 |
| 4 | symbols | 1.305 | 4.100 |
| 4 | customers | 3.773 | 8.357 |
| 4 | join | 24.357 | 6.357 |

### Evidence

| Run ID | Threads | Processes | Checked queries | CPU cap | Memory cap MiB |
| --- | --- | --- | --- | --- | --- |
| 929f9709167f481dbcac2545d9950267 | 1 | 18 | 1710 | 1.0 | 4096 |
| 369518a6fe0a4bc59a166f3bb6b55c24 | 4 | 18 | 1710 | 4.0 | 4096 |
