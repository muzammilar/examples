# Spark can read ClickHouse-written Iceberg snapshots

The 2026-11-23 read-only gate passes on native **Spark 4.1.4 / Iceberg 1.12.0**
(commit `212aa894e919ff9fdf89160da4aadcbe6858ac01`). It verifies **3,000 complete
records** in a pinned ClickHouse-written snapshot. **240 host controls pass**.
The source's native catalog snapshot remains unchanged.

After this example's five-command infrastructure lifecycle and the dependency
experiment that produces the probe table:

```sh
make connector-compactor-probe SOURCE_CASE=<passed-dependency-case-uuid>
```

The measured parent case is `5f1f06a7-c6cd-453c-ad78-2e83c816a523`. It must have
passed, retain its original table UUID/snapshot, and contain the recorded 3k probe.
The controller rejects invalid source IDs and unbounded/non-numeric native SQL
identifiers before launching a job. Images, network, mounts, source checksums,
2-CPU/2-GiB limits and process fencing use the existing local recovery helpers.
No host cloud credentials or public object storage are used.

This is a **compatibility prerequisite**, not the million-row concurrent
maintenance result. Spark uses the existing native Iceberg REST/S3 libraries;
Python coordinates their supported APIs. There is no custom Java helper and no
DuckDB application workload in this read-only gate. Actual application reads and
writes in the architecture continue to use the native C++ DuckDB client.

The oracle reads IDs **2,000,000–2,002,999** at the exact parent snapshot. It checks
count, distinct IDs, bounds and every customer's modulo value, integer cents and
SHA256 payload. There are four fields, **276,000 logical bytes** (numeric widths
plus 64-byte hash strings, excluding wire/storage overhead), 3,000 customer IDs,
no nulls and no RNG. Full body SQL took **3,170.630 ms** in the final fresh JVM;
that single observation includes Spark SQL planning/execution/collection and
excludes startup and the later footer/header audits. It establishes no throughput,
latency percentile, engine ranking or performance at scale.

All **three** pinned Avro manifests report `format-version=2` and omit the
key-value metadata property `content`. Native Avro header inspection checks the
actual files, rather than inferring this from an error message. Spark nevertheless
reads all bodies correctly. The [Iceberg v2 writing rules](https://iceberg.apache.org/spec/#version-2)
require that property. Reader acceptance therefore does **not** clear the writer
finding in [the manifest report](CLICKHOUSE_MANIFEST_ISSUE.md). DuckDB's recorded
rejection on the earlier ClickHouse-written fixtures remains a separate failure;
this gate did not rerun DuckDB against this exact 3k snapshot.

The same pinned scan's three data files were downloaded through native Iceberg
S3FileIO, bounded to **16 files / 2 MiB per file**, into ignored local results.
The existing Parquet library then inspected their local footers. This is read-only
on the backend, with diagnostic local-copy cost included:

| Files / rows | File bytes | Encoded uncompressed chunks | Compressed chunks | Ratio |
|---|---:|---:|---:|---:|
| 3 / 3,000 | 163,662 | 280,481 | 133,908 | 2.0946× |

The ratio concerns encoded column chunks, not logical input/file size. Local-copy
hashes, exact scan/input, native versions, manifest headers and resource samples
are bound in [the compact result](CONNECTOR_COMPACTOR_PROBE_RESULTS.json).

| Role | CPU core-seconds | CPU, one core = 100% | Observed seconds |
|---|---:|---:|---:|
| Spark probe | 11.216 | 137.62% | 8.15 |
| ClickHouse, background only | 3.150 | 33.05% | 9.53 |
| SeaweedFS | 0.142 | 1.49% | 9.54 |

Spark cgroup peak was **526.46 MiB**, including charged file/kernel memory. JVM and
Python process samples are retained separately and must not be added to that peak.
Backend peaks can predate this case. Native allocated bytes under the result mount
changed **1,171,456 → 1,433,600**; whole storage remained **3,541,712,896 bytes**, which
includes other retained tables/snapshots. Spark's sampled device 254:0 counters
observed **880,640 write bytes / 2 writes**, no reads; that device window was only
5.42 seconds. Full role/device windows, read/write byte rates and IOPS remain in
raw/compact evidence. These VM counters omit unobserved edges/delayed writeback;
warmed storage page cache does not establish a cold-I/O result.

The initial harness failure named its script `inspect.py`, shadowing Python's
standard library. It failed before table access and remains failed; the final
script has a distinct name, main guard and traceback capture. Intermediate body
and footer controls are retained separately, not pooled into a benchmark.

Next: keep independent hot and cold writers/readers running through a full rewrite
of a healthy two-million-row baseline. Verify real overlap with the native rewrite,
all inherited/new acknowledgements, current and retained snapshot bodies, native
file replacement, compression and resource costs. Preserve the immutable failed
source fixtures. Do not expire snapshots, vacuum or pause producers to obtain a pass.
