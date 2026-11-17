# RayforceDB versus DuckDB: embedded analytics in three languages

Compare the same pinned engines through **Python, C++ and Rust** over 2,000,000
seeded synthetic trades and 10,000 customers. Monitor CPU, memory and latency;
check every query result against an independent Python integer oracle.

## Quick start

1. `make docker-up` — verify source downloads and build both engines and all three drivers.
2. `make up` — generate or verify the example-owned fixture and reference results.
3. `make test` — run six full-data correctness cases and 18 negative controls.
4. `make down` — verify no application containers remain; preserve evidence and inputs.
5. `make docker-down` — remove this example's infrastructure and built image.

Docker Compose and host Python 3.11+ are required. No host C++/Rust compiler or
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
also validated every query at their actual thread count. Two controller regression
checks additionally passed: preserving partial evidence on a Docker timeout and
refusing to treat unknown runtime state as successful fencing.

## Measurement and limits

Measured **2026-11-17**, **Apple M4 Pro**, native **aarch64** Docker VM,
**11 CPUs / 24.42 GiB**. No other database containers were running. Three fresh
process trials per engine/language/profile; pair order reverses on alternate
trials. Each trial bulk-loads the same CSV, executes each of five queries once,
performs three warmup rounds, then measures **15 warm rounds**. Queries are submitted as SQL/Rayfall source strings on every call, without
prepared statements. Each measured query includes engine parsing/planning/execution and complete native integer
result conversion. Language-side expected-value comparison, printing and plan
parsing sit outside the query timer. Per-query process CPU sums user/system time
across threads; cgroup CPU/peak memory are read before container exit. Sampled
`docker stats` observations are retained too; fast cases can finish between
samples, so **cgroup `memory.peak` and CPU totals are authoritative**.

Warm latency tables report the **median of three trial p50s**, in milliseconds.
Raw per-query CPU/latencies, p99s (only 15 samples per trial, close to the maximum),
first-query observations, source fingerprints, exact image ID, cgroup memory
counters, runtime inspection and Docker samples live in ignored `results/runs/`.
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

<!-- measured-results -->

### 1 CPU / 1 engine thread

| Query | Python Ray ms | Python Duck ms | C++ Ray ms | C++ Duck ms | Rust Ray ms | Rust Duck ms |
| --- | --- | --- | --- | --- | --- | --- |
| totals | 25.760 | 2.521 | 25.537 | 2.264 | 24.457 | 2.215 |
| filtered | 11.973 | 2.277 | 10.201 | 2.097 | 9.600 | 2.049 |
| symbols | 3.750 | 11.138 | 3.607 | 11.063 | 3.523 | 10.890 |
| customers | 6.214 | 18.602 | 6.543 | 18.308 | 6.348 | 17.465 |
| join | 76.279 | 15.447 | 73.601 | 14.356 | 73.621 | 13.757 |

### 4 CPU / 4 engine threads

| Query | Python Ray ms | Python Duck ms | C++ Ray ms | C++ Duck ms | Rust Ray ms | Rust Duck ms |
| --- | --- | --- | --- | --- | --- | --- |
| totals | 6.558 | 1.045 | 6.700 | 1.121 | 6.548 | 1.043 |
| filtered | 3.563 | 1.068 | 3.089 | 1.121 | 3.068 | 1.021 |
| symbols | 1.198 | 3.673 | 1.191 | 3.744 | 1.210 | 3.581 |
| customers | 3.519 | 7.852 | 3.483 | 8.103 | 3.566 | 8.105 |
| join | 21.171 | 5.788 | 20.957 | 5.871 | 21.153 | 5.753 |

### Resource costs

Medians of three process runs. CSV timing covers the 2-million-row trade import; M rows/s uses that duration. Container CPU includes both imports, all 95 checked queries, runtime startup/shutdown and telemetry commands. RSS is the process high-water mark; cgroup peak also includes charged file cache/kernel memory and wrapper processes. These are whole-run peaks, not per-query retained heap sizes.

| CPUs | Language | Engine | CSV ms | M rows/s | CPU core-s | Peak RSS MiB | Cgroup peak MiB |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | python | rayforce | 140.54 | 14.23 | 2.514 | 368.6 | 414.0 |
| 1 | python | duckdb | 289.64 | 6.91 | 1.301 | 232.9 | 239.2 |
| 1 | cpp | rayforce | 139.60 | 14.33 | 2.455 | 361.7 | 409.5 |
| 1 | cpp | duckdb | 283.52 | 7.05 | 1.275 | 231.0 | 241.9 |
| 1 | rust | rayforce | 128.74 | 15.54 | 2.402 | 360.2 | 409.4 |
| 1 | rust | duckdb | 259.53 | 7.71 | 1.178 | 231.0 | 241.5 |
| 4 | python | rayforce | 43.15 | 46.35 | 2.652 | 388.1 | 433.7 |
| 4 | python | duckdb | 97.46 | 20.52 | 1.510 | 254.8 | 263.8 |
| 4 | cpp | rayforce | 46.36 | 43.14 | 2.617 | 381.2 | 430.0 |
| 4 | cpp | duckdb | 99.81 | 20.04 | 1.483 | 249.2 | 260.1 |
| 4 | rust | rayforce | 45.52 | 43.94 | 2.591 | 377.7 | 427.9 |
| 4 | rust | duckdb | 108.40 | 18.45 | 1.514 | 245.1 | 256.3 |

### Successful-query CPU time

C++ representative: median of each trial’s mean process CPU milliseconds per warm query, including native result conversion. CPU time sums all engine threads and may exceed wall latency. Python/Rust have the same raw counters in their result files.

| CPUs | Query | Ray CPU ms | Duck CPU ms |
| --- | --- | --- | --- |
| 1 | totals | 25.302 | 2.336 |
| 1 | filtered | 10.105 | 2.171 |
| 1 | symbols | 3.695 | 11.858 |
| 1 | customers | 6.546 | 18.213 |
| 1 | join | 75.265 | 14.982 |
| 4 | totals | 26.414 | 2.932 |
| 4 | filtered | 10.752 | 2.878 |
| 4 | symbols | 4.183 | 12.443 |
| 4 | customers | 8.964 | 24.692 |
| 4 | join | 77.809 | 19.389 |

### First query after fresh import

C++ medians across three fresh processes. Each query appears once in the fixed order shown, after CSV loading. This is **not an OS-cold-cache measurement**: loading and earlier queries have already touched pages.

| CPUs | Query | Ray first ms | Duck first ms |
| --- | --- | --- | --- |
| 1 | totals | 25.621 | 3.795 |
| 1 | filtered | 10.395 | 2.360 |
| 1 | symbols | 3.910 | 9.093 |
| 1 | customers | 6.563 | 24.148 |
| 1 | join | 80.213 | 15.851 |
| 4 | totals | 7.141 | 2.502 |
| 4 | filtered | 3.144 | 1.181 |
| 4 | symbols | 1.348 | 3.777 |
| 4 | customers | 3.724 | 7.756 |
| 4 | join | 22.902 | 6.215 |

### Evidence

| Run ID | Threads | Processes | Checked queries | CPU cap | Memory cap MiB |
| --- | --- | --- | --- | --- | --- |
| 6abdd9da06a14df498c41528c77661b3 | 1 | 18 | 1710 | 1.0 | 4096 |
| 81ff5cae4410417a862b82f7b4ae353b | 4 | 18 | 1710 | 4.0 | 4096 |
