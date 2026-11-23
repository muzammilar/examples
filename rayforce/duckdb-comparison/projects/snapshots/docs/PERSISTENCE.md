# Persisted snapshots: Rayforce and DuckDB

Use the five-command lifecycle in [README.md](../../../README.md). Before cleanup, run:

```sh
make test-persistence-capability  # two-row native file API gate
make test-rayforce-snapshot      # two million trades + 10,000 customers
make test-duckdb-snapshot        # same immutable fixture and oracle
```

Each command prints its owned result directory. Publish an explicit pair with
`make report-persistence RAY_RESULT=results/persistence/<ray-id>/result.json DUCK_RESULT=results/persistence/<duck-id>/result.json`.
The publisher checks source hashes, image identity, fixture/environment equality,
resource limits and complete trade/customer proofs. Raw state and diagnostics stay
ignored under `results/persistence/`; verified export copies are removed only after
fsynced proof metadata. Native database files remain available for further checks.

These profiles use local C++ applications built with CMake and the upstream native
C APIs, all inside this example. Python controls the experiment and independently
checks canonical CSV bytes. Existing Python/C++/Rust in-memory workloads also
passed regression checks after the adapter change.

The final regression exercised all six language/engine combinations, 18 native
negative controls and 17 controller tests. Raw evidence is retained in
`results/runs/d334963ad29f43939e4fb446d1edc9eb/`.

## Architecture and contract

Rayforce loads the existing CSV fixture, explicitly saves both tables with
`compress: true` and `durable: true`, exits, then reloads the saved columns in a
fresh process. `RAYFORCE_NO_FSYNC=0` is explicit and verified in each container's
effective environment. All nine data files have the pinned generation-2 encoded
I64 header, expected row counts and `RENC` payload marker. Header inspection is
metadata evidence; full native reload and canonical export validate actual values.

DuckDB opens an owned persistent file through `duckdb_open_ext`, loads the same
two tables, checkpoints and exits. A second process opens that file and runs the
same five grouping/filter/join checks plus complete ordered exports of both tables.
The in-memory C ABI remains available to the existing three language drivers.
Rayforce rejects the DuckDB file shortcut; it uses its explicit storage APIs.

The tables are immutable during these checks. Rayforce's two saves are sequential,
so this does not establish a multi-table atomic live backup. Neither graceful
close/reopen nor an encoded file establishes crash/power-loss durability. See
[persistence-contract.json](../../../config/persistence-contract.json) for the distinct saved-table,
synchronous-journal and asynchronous observation profiles.

## Measured correctness and footprint

**2026-11-21**, Apple M4 Pro, native ARM64 Docker Desktop VM. Both applications:
**1 CPU / 4 GiB**, swap disabled, one engine thread. One matched saved-snapshot
observation per engine; these are functional-test costs, not repeated capacity or
latency rankings. Input is synthetic, non-null numeric data: six I64 trade columns,
2,000,000 distinct trade IDs, 10,000 customers, 256 symbols and 30 day values;
customer/other attributes use the retained `Random(20261117)` fixture. Three I64
customer columns add 10,000 rows.

- Trade CSV: **52,801,261 bytes** (50.36 MiB), SHA256
  `c8756870bdbb238b69ef8d563c6d4f176753e3864ffa50f508e707ed94406de0`.
- Both backends returned **all 2,000,000 trade bodies and 10,000 customer bodies**,
  byte-for-byte equal to the independent inputs; all five query outputs matched.
- Dense I64 values alone occupy **96,240,000 bytes**, excluding engine metadata.

| Engine | Settled native files, bytes | Import/save/check CPU core-s | Reopen/check/export CPU core-s | Cgroup peaks for those processes, MiB |
|---|---:|---:|---:|---:|
| Rayforce | 13,031,576 | 0.388 | 0.736 | 406.3 / 356.9 |
| DuckDB | 14,954,496 | 0.566 | 0.503 | 191.7 / 155.4 |

Single-call elapsed observations (one sample each): Rayforce trade save
**142.805 ms**, customer save **3.203 ms**, trade reopen **3.942 ms**, customer
reopen **2.094 ms**; DuckDB trade CTAS **496.669 ms**, customer CTAS **8.063 ms**
and checkpoint **1.847 ms**. These have different boundaries and are not a
matched latency ranking or a tail-latency distribution.

Native file totals include each format's catalog/schema/index overhead and any
retained native WAL, excluding verification CSVs. They are whole-store footprints,
not pure codec ratios. DuckDB's `storage_info` output reports the observed codecs;
Rayforce's native encoded headers and per-column file sizes are retained in
[PERSISTENCE_RESULTS.json](../reports/PERSISTENCE_RESULTS.json). Off-path gzip level 1 of the
same verified trade CSV produced **24,337,454 bytes**, **2.170×** compressibility;
its CPU/time costs are separate from engine compression and stored in the JSON.

CPU utilization, process peak RSS, cgroup peaks, block read/write bytes/operations,
sampled file growth and controller costs are retained per process. Directory
samples are non-atomic, can race renames and can miss peaks; the settled file sizes
are separate. Docker Desktop bind mounts can perform host I/O outside the container
counters. PID/process RSS and charged cgroup memory describe different boundaries.

Do not compare DuckDB's checkpoint-only timer with Rayforce's full save timer:
DuckDB already writes during CTAS. Whole-process figures above include import,
storage work and verification, or reopen plus queries/export. DuckDB's initial
file-open cost is inside its process totals but not an isolated query timer;
Rayforce's explicit table-load calls have their own plan records. New engine
processes do not imply an OS-cold cache. Native automatic compression CPU is mixed
with other engine work; the CSV gzip measurement is off-path.

## Remaining production work

Live independent writers/readers, an external fsynced per-operation intent/ACK
journal, synchronous versus asynchronous recovery, SIGKILL, interrupted snapshot
replacement, torn tails versus interior corruption, writer fencing, native repair,
backup restoration, pressure and repeated performance/cache-temperature matrices
are outside this saved-table profile. The separate [journal test](../../rayforce-journal/docs/JOURNAL.md)
now covers live synchronous Rayforce IPC, two SIGKILLs and a second snapshot/tail;
separate tests now cover [ownership and filesystem locks](../../rayforce-journal/docs/FENCING.md),
[offline restoration](../../rayforce-journal/docs/JOURNAL.md#offline-restore-into-separate-storage),
[known torn-tail repair](../../rayforce-journal/docs/TAIL_RECOVERY.md), [payload integrity](../../rayforce-journal/docs/CORRUPTION.md)
and [middle-header damage](../../rayforce-journal/docs/HEADER_CORRUPTION.md), preserving failed outcomes.
Asynchronous recovery, interrupted snapshots, live backups, pressure and repeated
cache-temperature matrices remain unrun. No clustered, HA, general transaction-atomicity or physical-power-loss
claim is made for this saved-table profile. Raw development probes remain separate
from the explicitly selected result pair; failures must not be reset into passes.

Sources: [Rayforce disk-table API](https://rayforcedb.com/docs/namespaces/db/),
[pinned save options](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/ops/system.c),
[pinned column format](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/col.h),
[encoded headers](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/encfmt.h),
[DuckDB C API](https://duckdb.org/docs/2.0/clients/c/connect),
[DuckDB storage information](https://duckdb.org/docs/2.0/configuration/pragmas#storage-information).
