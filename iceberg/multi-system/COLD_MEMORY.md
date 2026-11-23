# Native cold-read memory isolation

2026-11-23: the memory error also occurs in **sequential native ClickHouse
queries**, without DuckDB, concurrent writers or a running compactor. This
narrows the availability finding; it does not explain the separate DuckDB
wrong-answer bug or establish that ClickHouse has a memory-management defect.

After this example's normal infrastructure lifecycle, the diagnostic target is:

```sh
make cold-memory-diagnostic CASE=fe20a081-ab77-4c71-9ecb-c2332dce4a7e
```

The executed trial invoked that target's Python entry point directly:
`python3 cold_memory.py fe20a081-ab77-4c71-9ecb-c2332dce4a7e`.
It reads the retained table's **2,097,000 acknowledged rows**, checking every ID,
customer, amount and SHA256 body plus uniqueness and complete 1,000-row batches.
The native catalog UUID, snapshot and metadata pointer are checked against the
witness; the pointer/snapshot and service identities remain unchanged afterward.
It performs no DDL, inserts, compaction, cleanup, cache clearing or limit changes.

The query keeps its 768 MiB query limit, 15-second execution limit and full scan
bounds. The existing server limit stays **1 GiB**, with a **2 CPU / 1.5 GiB**
container limit. Each request opens a fresh native clickhouse-client process;
the server stays running. Order rotates across three rounds, with no warmup and
uncontrolled engine/OS caches. These are diagnostic attempts, not three fresh
engine trials or a capacity comparison.

| Query variant | Successful / attempted | Successful request wall times (ms) |
|---|---:|---|
| Baseline, default reader settings | 3/3 | 808.436, 674.546, 719.874 |
| Parquet high watermark 64 MiB | **2/3** | 786.483, 669.531 |
| Same watermark plus `max_threads=1` | 3/3 | 682.859, 764.594, 675.343 |

The failed watermark-only request returned error **241** after **506.264 ms**:
the server would use 1.01 GiB, with RSS 1,023.80 MiB and an attempted 7.38 MiB
allocation. Its stack context includes evaluation of the full-body predicate.
The baseline successes do not erase the original live failures; three
single-threaded successes do not establish a reliable mitigation. Wall time
includes Docker exec, client startup/connection, SQL, response and host handling.
No tail-latency or throughput winner is claimed.

## Counters and limits

All eight successful results verify 2,097,000 unique rows, zero invalid bodies,
zero partial batches and all 2,097,000 acknowledged rows. The
[compact result](COLD_MEMORY_RESULTS.json) retains all nine outcomes, environment,
resources and 44 raw artifact hashes. The source saved in the raw case matches
the tracked diagnostic byte for byte.

Across the sampled whole diagnostic, ClickHouse used **10.522 core-seconds**
(168.36% of one core over 6.25 s), with sampled charged memory peaking at
**1,490.51 MiB**. SeaweedFS used **1.487 core-seconds** (23.94% over 6.21 s),
peaking at **965.23 MiB**. These include probes/background activity and are not
per-variant costs. Lifetime peaks are separately recorded and may predate this
run; charged cache/kernel memory is not process RSS.

ClickHouse allocated bytes change from **2,857,922,560 to 2,869,116,928**;
SeaweedFS remains at **3,651,411,968**. Database logs/background work may change
allocated bytes even though the Iceberg snapshot does not change. Device 254:0
reports **8,192 read bytes / 2 read operations** and zero write counters for
ClickHouse over the sampled window; SeaweedFS reports zeros. These are VM device
observations with cache effects, not zero logical S3 activity or physical SSD
latency. Per-second rates and sampling gaps are in the JSON. Client/controller
CPU, physical I/O latency and a new exact-snapshot footer compression measurement
are unavailable here; the earlier before/after codec audit is a separate snapshot
scope. No codec or data-file rewrite was performed. This diagnostic does not
qualify a holistic performance comparison.

The running version's settings report a **4 GiB default** Parquet high watermark
and two query threads. The watermark is an approximate reader scheduling budget,
not a total server memory cap. This matches the inspected upstream
[setting definition](https://github.com/ClickHouse/ClickHouse/blob/master/src/Core/FormatFactorySettings.h)
and [format configuration path](https://github.com/ClickHouse/ClickHouse/blob/master/src/Formats/FormatFactory.cpp).
Those links are moving upstream source; the version-matched setting descriptions
are saved in the raw trial. Reducing the watermark cannot bound all expression,
aggregation and server allocations. Per `agent-query-safety`, keep throwing
execution/memory/scan bounds rather than accepting partial output.

Output-block sizing is tested in the follow-up below; concurrent operation at
fixed caps remains unqualified. The original **32/51 failed cold
reads**, ordinary connector wrong answers and manifest compatibility failures
remain unresolved. This level-5 investigation is approved for this architecture
only; no other system's performance depth is implicitly approved.

## Output-block follow-up: also failed

The subsequent executed command was:

```sh
python3 cold_memory.py fe20a081-ab77-4c71-9ecb-c2332dce4a7e --blocks
```

The corresponding Make target is `make cold-memory-blocks CASE=<case UUID>`.
It keeps the same full 2,097,000-row checks, fixed caps, three rotating rounds and
unchanged native snapshot. The [follow-up result](COLD_MEMORY_BLOCK_RESULTS.json)
preserves raw SQL, outputs, errors, source and resource counters separately.

| Variant (all use the 64 MiB watermark) | Successful / attempted | Failure wall time |
|---|---:|---:|
| Default output block / threads | 3/3 | None observed |
| Parquet block 8,192 rows | **2/3** | 373.080 ms |
| Same block plus one query thread | **2/3** | 270.544 ms |

Both failures are server total-memory error 241; both variants previously or
subsequently return correct full bodies. **Single-threaded execution is not a
validated memory workaround.** The initial single-threaded three-pass observation
remains recorded, alongside this counterexample. All seven successful requests
in this follow-up reconcile every acknowledged body.

Whole-window ClickHouse CPU is **9.428 core-seconds**, mean **174.27%** of one
core over 5.41 s; sampled charged memory peaks at **1,472.68 MiB**. SeaweedFS uses
**1.291 core-seconds**, **25.32%** over 5.10 s and **997.71 MiB** sampled peak.
ClickHouse allocated bytes go from **3,162,595,328 to 3,174,469,632**; SeaweedFS
remains **3,651,411,968**. Device counters report ClickHouse **12,288 read /
44,396,544 write bytes**, **3 read / 7,576 write operations**, approximately
2,271 read bytes/s and 8,206,385 write bytes/s. SeaweedFS device counters remain
zero. The same cache, counter-window, compression and client-cost limitations
above apply; these are not per-variant performance costs.

Additional bounded native observations taken **after** the run show
`MemoryTracking=858,824,232` bytes, `jemalloc.allocated=426,723,440`,
`jemalloc.active=646,578,176` and `MemoryResident=1,429,635,072`. The process-list
snapshot shows only its own diagnostic query and no background merges/fetches in
the separately sampled counters. These asynchronous, differently scoped values
do not partition memory exactly or prove a leak. Server-wide baseline memory
needs investigation before attributing the failures to the Parquet reader alone.
