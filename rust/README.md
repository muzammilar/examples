# Rust examples

- [`hello-web-app`](hello-web-app): Actix web server and CLI sharing a library.
- [`kv-store`](kv-store): replicated key-value store using openraft over a write-ahead log, with a Docker Compose cluster.
- [`embedded-databases`](embedded-databases): Fjall/redb durable atomic catalog pairs, independent Rust readers/writers, owner SIGKILL/reopen, full acknowledged-body reconciliation and offline fresh-copy restore. Docker builds the pinned Cargo project; no host Rust toolchain is required.

The [100,000-pair checkpoint](embedded-databases/config/RESULTS_100K.json) verifies
**101,550 Fjall / 101,561 redb cumulative acknowledged pairs**, including the
100,000-pair seed on each engine and preserved earlier live histories. Both lose
zero acknowledged data and return no wrong or partial pairs. CPU, memory,
compression, disk allocation, read/write bytes and IOPS accompany the results.
These are process-recovery observations under shared-host functional load, not a
performance ranking. Neither is a SQL-compatible SQLite replacement.

The [million-pair qualification](embedded-databases/config/RESULTS.json) additionally
verifies **1,000,789 Fjall / 1,000,778 redb acknowledged pairs**, with zero loss or
wrong/partial bodies after owner crash and fresh-copy restore. One redb intent
committed without an acknowledgement and remains classified as unacknowledged.
Each backend starts with **528,000,000 logical key/value bytes**, before native
metadata or export framing. These larger-fixture results remain functional
recovery observations under the same 2-CPU/2-GiB service budget.

Later passes cover Fjall compaction/prefix scans, redb conflicting updates and
snapshot space pinning, format migration, pressure and isolated repeated
comparisons with SQLite/RocksDB. These libraries do not supply native clustering
or replicated HA in this example. See the [candidate research](EMBEDDED_DATABASE_SURVEY.md)
for pinned source findings; Turso remains a separate future SQL example.

Planned: [CSV/Parquet/Arrow IPC/Vortex comparison](../docs/example-plans/columnar-formats.md),
covering typed correctness, compression, scan/decode costs, resources and interrupted
file publication. The project is not implemented or benchmarked yet.
