# CSV, Parquet, Arrow IPC and Vortex

Planned, unrun comparison, extended with Vortex at the user's request on
2026-11-26. Implement as a self-contained Rust project under
`rust/columnar-formats/`, with Docker-built programs and the five-command Compose
lifecycle. This is a file-format experiment, not a distributed database or an HA
claim. Existing Spark Arrow-based Parquet verification is not an Arrow IPC benchmark.

## Focused examples

- [ ] **Encode and reopen the same data.** Generate a deterministic, configurable
  two-million-row typed fixture once, including nulls, repeated strings and
  less-compressible values. Write CSV, Parquet, Arrow IPC file/Feather V2 and
  Vortex with maintained libraries. Fresh readers must verify every value, type,
  null and ID against an independent canonical oracle before timing is qualified.
- [ ] **Projection, filtering and full scans.** Materialize the same selected
  values and exact aggregates, with a full scan, a narrow projection and a
  selective predicate. Compare one ordered and one shuffled layout where useful;
  record actual bytes read and effective pruning. Opening a footer or counting
  metadata rows does not qualify as reading/decoding the dataset.
- [ ] **Interrupted publication and damaged files.** Keep a reader on the last
  verified immutable file while killing a writer before close/atomic publication.
  Reopen preserved truncated/footer-damaged copies and record native errors versus
  independently detected wrong values. Test the declared checksum coverage;
  absent native corruption detection is a limitation, not an invented guarantee.

## Fair comparison and measurements

Use the same schema, logical rows, ordering, null conventions, CPU/memory caps,
worker counts and materialized outputs. Treat text parsing/conversion, native
array access, zero-copy/mmap access and query-engine execution as separate paths.
Use one common query engine only where its stable integrations support all
selected formats; otherwise label the different readers and avoid attributing an
engine difference solely to a format.

Report native encodings, dictionary use, codecs/levels, row-group/chunk sizes,
footer/index bytes and write-buffer budgets. Separate uncompressed references,
recommended native defaults and a small explicit codec variant; do not silently
wrap a format in whole-file compression and then compare its range-read behavior.
Arrow means **persisted IPC/Feather V2** here. Its in-memory layout/zero-copy costs
are a separate measurement, not an on-disk compression result.

Measure write/close/publication time and full read/decode/query latency in ms or
µs, throughput, CPU utilization/core-seconds, memory peak, logical/file/allocated
bytes, compression, disk read/write bytes and rates, IOPS and network when used.
Count all metadata and copies. Separate warm reads from verified cold-data reads;
a fresh process alone does not clear the shared kernel page cache. Never drop
caches globally on the host. Keep repeats and variants bounded to level 3 and
report missing measurements and uncertainty without selecting a premature winner.

## Vortex compatibility and source checks

Vortex 0.95.0 was the latest non-prerelease on 2026-11-26; recheck stable versions
when implementing. Pin the Rust/Arrow reader-writer combination and the emitted
Vortex edition/components. Verify read compatibility with intended consumers;
being Arrow-compatible does not make a Vortex file an Arrow IPC or Parquet file.
Its native scan API exposes projections, filters and row ranges, which need their
own measured read/decode controls rather than a metadata-only speed comparison.

- [Vortex Rust quickstart](https://docs.vortex.dev/getting-started/rust)
- [Released file/scan implementation](https://github.com/vortex-data/vortex/blob/0.95.0/vortex-file/src/lib.rs)
- [Vortex file specification](https://docs.vortex.dev/specs/file-format)
- [Vortex edition compatibility](https://docs.vortex.dev/specs/versioning/compatibility)
- [Arrow memory and serialization format](https://arrow.apache.org/docs/format/Columnar.html)
