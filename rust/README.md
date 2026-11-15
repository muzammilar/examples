# examples-rust
Example codes in rust for fun

## Examples

- [`hello-web-app`](hello-web-app): Actix web server and CLI sharing a library.
- [`kv-store`](kv-store): Replicated key-value store: openraft over a write-ahead log, with a Docker Compose cluster.

## Planned embedded database project

Future `rust/embedded-databases`: one Cargo project with separate adapters and the
same deterministic datasets, durable commit rules, throughput/latency measurements
and external acknowledged-write reconciliation after a killed process. These
engines are in-process libraries; do not invent native replication or cluster HA.

| Engine | Potential example | Comparison |
|---|---|---|
| [Fjall](https://github.com/fjall-rs/fjall) | LSM ingestion, atomic batches, prefix/range scans during compaction, durable kill/reopen | redb / RocksDB |
| [redb](https://github.com/cberner/redb) | Transactional catalog updates, snapshot readers, writer contention and crash recovery | Fjall / SQLite |

Documentation backlog only; these examples are deferred until the current
StarRocks, Neon and GreptimeDB batch is finished.
