# RayforceDB

Website: https://rayforcedb.com/

GitHub: https://github.com/RayforceDB/rayforce

| Example | What it demonstrates |
|---|---|
| [`duckdb-comparison/`](duckdb-comparison/README.md) | Embedded numeric analytics against DuckDB through Python, C++ and Rust; 2 million trades, 10,000 customers, exact query results, repeated 1/4-CPU runs, CPU/memory time series, native peaks and disk-I/O/storage accounting |

## Measured comparison

2026-11-21 rerun, Apple M4 Pro, native ARM64 Docker VM. In the four-CPU C++ run,
Rayforce's warm median was **1.251 ms vs DuckDB 3.809 ms** for 256-group
aggregation (**3.04×** lower latency), and **3.635 ms vs 8.345 ms** for 10,000
customer groups (**2.30×** lower latency). DuckDB's join was **6.218 ms vs
Rayforce 21.210 ms** (**3.41×** lower latency). Median process peak RSS for the
complete workload was **247.6 MiB vs Rayforce 380.8 MiB** (**1.54×** lower).
See the example for all three languages, one-CPU results, input checks,
source pins, resource measurements and limitations; this is not a universal ranking.
Exported kernel block counters were zero for these cached in-memory runs; this
does not compare physical disk performance. Persisted-storage compression remains
outside this profile.

The [ingestion comparison](duckdb-comparison/README.md#ingestion-comparison) adds
144 fresh-process trials with 100,000 appends each, batches of 1–10,000 and
full verification of all 2,100,000 final rows per trial. In the one-CPU C++
10,000-row typed path, median observed rates were 19,999,500 rows/s for
Rayforce and 7,032,472 for DuckDB’s row-wise Appender. CPU, memory and latency
costs accompany the rates. These in-memory results do not establish equivalent
transaction isolation, durability or sustained capacity; the columnar Appender
is now measured in a separate [columnar comparison](duckdb-comparison/README.md#columnar-appender-comparison):
144 further trials, with C++ large-batch DuckDB rates of **10.87 million rows/s**
(columnar) versus **6.58 million** (row-wise) in the same run. Rayforce uses
the identical typed implementation in both paths; its columnar-labeled median
was **20.36 million rows/s**. See the trial spread and resource costs before
generalizing from these bounded, single-writer measurements.

## Limits and remaining experiments

This example embeds the engine and uses its native worker pool, not a distributed
cluster. The Rayforce source pin is a development commit after v3.0.2; the
benchmark does not qualify another release. Full production testing,
broader failure/restore boundaries, graph/as-of workloads, and comparisons using official
language-specific DataFrame interfaces remain unrun. Consult the pinned
[upstream architecture](https://github.com/RayforceDB/rayforce/tree/38fd4be3560d63a5514c1e6883685fe496d45e1a/docs/docs/architecture)
before inferring storage or replication guarantees from in-memory query results.

The [saved-snapshot profiles](duckdb-comparison/PERSISTENCE.md) now verify
2,000,000 trade bodies and 10,000 customer bodies after a fresh-process reopen
on both engines. Observed native files were 13,031,576 bytes for Rayforce and
14,954,496 bytes for DuckDB, with CPU/memory/compression/I/O evidence. These
immutable snapshot checks do not qualify live journal or crash recovery.
The separate [synchronous journal test](duckdb-comparison/JOURNAL.md) now recovers
2,251 live ACKs and the full seed after two SIGKILLs, with independent writers,
readers and an external witness. No acknowledged body loss was observed;
asynchronous recovery, online backup and matched DuckDB recovery remain unrun.
An [offline snapshot/tail restore](duckdb-comparison/JOURNAL.md#offline-restore-into-separate-storage)
also rebuilt every body in separate local storage without access to original input
CSVs: 1,614 replayed entries, zero evaluation errors and zero ACK loss. This does
not establish online backup availability or independent-host durability.

[Journal ownership tests](duckdb-comparison/FENCING.md) now reject all ten native
second openers on Docker-managed ext4 storage while writers/readers continue.
The host-share bind mount failed both native and standard-library locking controls;
that failure remains reported. Current live tests use uniquely owned volumes, and
the two-crash volume rerun recovered all 2,255 ACKs with zero observed body loss.

[Controlled torn-tail recovery](duckdb-comparison/TAIL_RECOVERY.md) now rejects
startup, validates 1,621 intact frames and removes only the known injected three
bytes in a fresh copy. It restores 2,000,000 seed trades, 10,000 customers and
2,262 live ACKs with zero missing/wrong bodies. `make test-production-recovery`
creates its own recovery source and runs the six recovery cases serially.

The [interior payload-corruption probe](duckdb-comparison/CORRUPTION.md) exposes
a **failed integrity boundary**: native replay accepts a validly encoded one-bit
price change and returns one wrong acknowledged body. An invalid serialized type
fails closed; an untouched backup restores all 2,000,000 seed trades, 10,000
customers and 2,269 ACKs correctly. The default `make test-production`
propagates this failure. This logical edit on a fenced copy does not establish
physical bit-rot behavior or normal process-crash data loss. General corruption
repair remains unqualified; the distinct CRC-protected AOF subset is evaluated below.

The [middle-header diagnostic](duckdb-comparison/HEADER_CORRUPTION.md) confirms
startup fails closed for one-bit magic/version damage. Native prefix validation
does not identify a safe recovery boundary: following its truncation hint on a
disposable copy loses **566 of 2,259 ACKs** despite clean startup. Our guard
rejects both edits; the intact backup recovers every body. This is a logical
file-edit experiment, not evidence of physical bit rot or ordinary crash loss.

The [native AOF profile](duckdb-comparison/AOF.md) calls the public API from C++
over 10,000 opaque 1,024-byte events. Payload corruption is rejected, while length
and commit-marker edits yield successful opens/scans missing **5,000 / 5,000 /
100 acknowledged records**. The strict file-edit integrity verdict remains
**FAILED**; an intact native backup verifies every body. CPU, memory, I/O,
compression, disk and native API timing are reported separately from buffered
admission versus commit barriers. This is an application event-log API, not
automatic database persistence; live AOF process-crash and rotation tests remain open.

The matching [DuckDB live WAL profile](duckdb-comparison/DUCKDB_RECOVERY.md),
qualified on 2026-11-22, uses native C++ Quack sessions with TLS and the same
two-million-trade / 10,000-customer input. Both retained-volume crashes recovered
all **2,364 ACK bodies**, with **zero loss/corruption**. Checkpointing keeps readers
and writers active. CPU/memory, native codecs/archive compression, disk/block I/O
and recovery observations are published. This is one functional observation,
not a latency/capacity winner; native protocols and logged operations differ.
