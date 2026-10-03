# QuestDB

Website: https://questdb.com/

- [`single-node/`](single-node) — one server from the official image on Docker Compose (web console, PG wire, ILP over HTTP and TCP), with a SQL walkthrough: designated timestamp, daily partitions, WAL tables, `SAMPLE BY` with `FILL`, `LATEST ON`, `ASOF JOIN` with `TOLERANCE`, and `DEDUP UPSERT KEYS` exercised over both SQL and ILP.
- [`market-data-showcase/`](market-data-showcase) — live quotes and trades over ILP/HTTP from Rust feeds (official `questdb-rs` client) while PG-wire clients (tokio-postgres) run `SAMPLE BY` bars, VWAP, `LATEST ON` top of book and a trades→quotes `ASOF JOIN` on the newest second; compares one in-order feed with four interleaved feeds.

There is no cluster example: open-source QuestDB is a single server. Primary-replica
replication (one writer, read replicas, through an object store) is a QuestDB Enterprise feature
([docs](https://questdb.com/docs/operations/replication/)), and there is no open-source
sharding or multi-primary mode. What the open-source clients do offer is client-side failover
across several addresses, which still needs replicated servers behind it.

No free edition replicates either (checked 2026-10-03). The only way to try replication is the
[QuestDB Enterprise trial](https://questdb.com/enterprise/trial/): 30 days, requested through a form
with a work email and sent by email, for internal non-production evaluation only
([terms](https://questdb.com/legal/enterprise_trial_terms)). There is no OSS replication fork, and
the official [`questdb-ha-reads`](https://github.com/questdb/questdb-ha-reads) sample only fails
over between independent, unreplicated servers.

## Benchmark

ILP over HTTP from 8 Python senders, then timed queries, 20M rows (6.7M trades + 13.3M quotes, 100 symbols, 3 days), server capped at 4 CPUs / 8 GB (Apple M4 Pro, Docker VM aarch64, 2026-10-02, shared VM, four runs): 2.2–3.1M rows/s acked into the WAL and 1.9–2.8M rows/s visible after the WAL apply, even with the senders' rows interleaved in time. One-hour interval aggregates, `count()` and `LATEST ON` answer in ~1 ms, 1-minute OHLCV for one symbol over 3 days in 17–29 ms, hourly VWAP for 100 symbols over all trades in 74–169 ms; `ASOF JOIN` trades→quotes takes 61–172 ms for an hour and 1.3–3.0 s for a full day (single-threaded). Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).

Live showcase (same machine, no caps): at 500k rows/s from one in-order feed, the newest trade is queryable 25 ms (p50) after it was generated and 4 clients run 369 queries/s on the newest data: top of book 0.1 ms, 1-minute bars and VWAP 2–5 ms, a 1 s trades→quotes `ASOF JOIN` 21 ms (p99 82 ms). The same rate from 4 interleaved feeds forces out-of-order merges: query throughput halves and the ASOF p99 goes to 2.6 s. Unthrottled, 4 feeds ingest ~2M rows/s. Details: [`market-data-showcase/README.md`](market-data-showcase/README.md#sample-output).

## Known issues

QuestDB is actively developed (10.0.0 in August 2026, 10.0.1 three weeks later, nightly images
daily); nothing here blocked the examples. Seen while building them (2026-10-02, QuestDB 10.0.1,
`questdb==5.0.0`, `questdb-rs` 7.0.0):

- An empty `QDB_*` variable stops the server at startup (exit 55):
  `io.questdb.ServerConfigurationException: invalid configuration value [key=shared.worker.count, value=]`
  (from `QDB_SHARED_WORKER_COUNT=`). Leave a key out instead of setting it empty.
- Durations in `SAMPLE BY` and `TOLERANCE` take single-letter units: `SAMPLE BY 100ms` fails with
  `expected single letter qualifier`. Milliseconds are `T` (`SAMPLE BY 250T`, `TOLERANCE 300T`).
- `round(x, 2)` does not always return the nearest double, so psql shows values like
  `64765.270000000004`. The walkthrough generates prices as whole cents `/ 100.0` and casts
  computed results to `decimal(18,2)`.
- When the Docker VM disk is full, `CREATE TABLE` over `/exec` fails with only
  `Could not create table, could not create [dir=/var/lib/questdb/db/bench_trades~14]`; the
  server log has the cause (`CairoException: [28]`, ENOSPC). The benchmark drops its tables
  at the end (~925 MiB for 20M rows) for this reason.
- `ASOF JOIN` runs single-threaded (`AsOf Join Fast` in `EXPLAIN`), so a join over one day of
  2.2M trades takes 1.3–3.0 s while the parallel `SAMPLE BY` over all 6.7M trades takes under
  0.2 s. Narrowing the quotes side with a subquery (`ASOF JOIN (SELECT * FROM quotes WHERE ts > …)`)
  made it slower (626 ms vs. 299 ms in one test), so the queries join the table directly.
- Out-of-order WAL ingest hurts concurrent queries. In the showcase, 4 feeds writing
  overlapping time ranges into one table push the 1 s `ASOF JOIN` p99 from ~80 ms to 2.6–8.6 s.
  Unthrottled with 8 feeds, one ASOF query was aborted with `timeout, query aborted
  [fd=1230508435439, runtime=60001ms, timeout=60000ms]` and the newest visible data lagged
  4–8 s. Writes in time order (one writer per table) avoid this.
- After a table is dropped and recreated, statements cached for open PG connections log
  `E ... cached query plan cannot be used because table schema has changed`. QuestDB recompiles
  them and the client sees no error, so this is log noise.
- `questdb-rs` 7.0.0 built with `default-features = false` and only `sync-sender-http` fails
  with `error: At least one of tls-webpki-certs or tls-native-certs features must be enabled.`,
  even for plain HTTP; the showcase also enables `tls-webpki-certs` and `ring-crypto`. The crate
  needs Rust 1.91.1 or newer.
- The Python client 5.0.0 warns `DeprecationWarning: questdb.ingress is deprecated; import from
  questdb instead (or use questdb.connect() for QWP/WebSocket)`; the benchmark imports
  `from questdb import Sender`.
