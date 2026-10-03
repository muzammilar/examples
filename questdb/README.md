# QuestDB

Website: https://questdb.com/

| folder | what |
|---|---|
| [`single-node/`](single-node) | One server from the official image on Docker Compose (web console, PG wire, ILP over HTTP and TCP). SQL walkthrough: designated timestamp, daily partitions, WAL tables, `SAMPLE BY` with `FILL`, `LATEST ON`, `ASOF JOIN` with `TOLERANCE`, `DEDUP UPSERT KEYS` over both SQL and ILP. |
| [`market-data/`](market-data) | Live quotes and trades over ILP/HTTP from Rust feeds (official `questdb-rs` client) while PG-wire clients (tokio-postgres) run `SAMPLE BY` bars, VWAP, `LATEST ON` top of book and a trades→quotes `ASOF JOIN` on the newest second. Compares one in-order feed with four interleaved feeds. |

## No cluster example

- Open-source QuestDB is a single server. No open-source sharding or multi-primary mode.
- Primary-replica replication (one writer, read replicas, through an object store) is a QuestDB Enterprise feature ([docs](https://questdb.com/docs/operations/replication/)). No free edition replicates (checked 2026-10-03), and there is no OSS replication fork.
- The only way to try it is the [QuestDB Enterprise trial](https://questdb.com/enterprise/trial/): 30 days, requested through a form with a work email and sent by email, internal non-production evaluation only ([terms](https://questdb.com/legal/enterprise_trial_terms)).
- Open-source clients offer client-side failover across several addresses, which still needs replicated servers behind it. The official [`questdb-ha-reads`](https://github.com/questdb/questdb-ha-reads) sample only fails over between independent, unreplicated servers.

## Benchmark

Apple M4 Pro, Docker VM aarch64, shared VM.

| example | setup | result | details |
|---|---|---|---|
| single-node | 2026-10-02, four runs; ILP over HTTP from 8 Python senders, 20M rows (6.7M trades + 13.3M quotes, 100 symbols, 3 days); server 4 CPUs / 8 GB | Ingest: 2.2–3.1M rows/s acked into the WAL, 1.9–2.8M rows/s visible after WAL apply, with senders' rows interleaved in time. Queries: one-hour interval aggregates, `count()` and `LATEST ON` ~1 ms; 1-minute OHLCV for one symbol over 3 days 17–29 ms; hourly VWAP for 100 symbols over all trades 74–169 ms; `ASOF JOIN` trades→quotes 61–172 ms for an hour, 1.3–3.0 s for a full day (single-threaded). | [single-node](single-node/README.md#benchmark) |
| market-data | no caps; 500k rows/s | One in-order feed: newest trade queryable 25 ms (p50) after generation; 4 clients run 369 queries/s on the newest data (top of book 0.1 ms, 1-minute bars and VWAP 2–5 ms, 1 s trades→quotes `ASOF JOIN` 21 ms, p99 82 ms). 4 interleaved feeds at the same rate force out-of-order merges: query throughput halves, ASOF p99 2.6 s. Unthrottled, 4 feeds ingest ~2M rows/s. | [market-data](market-data/README.md#sample-output) |

## Known issues

QuestDB is actively developed (10.0.0 in August 2026, 10.0.1 three weeks later, nightly images daily); nothing here blocked the examples. Seen 2026-10-02 with QuestDB 10.0.1, `questdb==5.0.0`, `questdb-rs` 7.0.0. Details in each example's Known issues:

| issue | example |
|---|---|
| An empty `QDB_*` variable stops the server at startup (exit 55) | [single-node](single-node/README.md#known-issues) |
| `SAMPLE BY` / `TOLERANCE` take single-letter units: `ms` fails, use `T` | [single-node](single-node/README.md#known-issues) |
| `round(x, 2)` returns values like `64765.270000000004` | [single-node](single-node/README.md#known-issues) |
| Full Docker VM disk: `CREATE TABLE` fails with only `Could not create table` | [single-node](single-node/README.md#known-issues) |
| `ASOF JOIN` is single-threaded; one day of 2.2M trades takes 1.3–3.0 s | [single-node](single-node/README.md#known-issues) |
| `cached query plan cannot be used` log noise after drop/recreate | [single-node](single-node/README.md#known-issues) |
| Python client 5.0.0 `DeprecationWarning: questdb.ingress is deprecated` | [single-node](single-node/README.md#known-issues) |
| Out-of-order WAL ingest from several writers hurts concurrent queries | [market-data](market-data/README.md#known-issues) |
| `questdb-rs` 7.0.0 needs a TLS feature even for plain HTTP, and Rust 1.91.1+ | [market-data](market-data/README.md#known-issues) |
