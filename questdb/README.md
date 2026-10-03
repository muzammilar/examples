# QuestDB

Website: https://questdb.com/

- [`single-node/`](single-node) — one server from the official image on Docker Compose (web console, PG wire, ILP over HTTP and TCP), with a SQL walkthrough: designated timestamp, daily partitions, WAL tables, `SAMPLE BY` with `FILL`, `LATEST ON`, `ASOF JOIN` with `TOLERANCE`, and `DEDUP UPSERT KEYS` exercised over both SQL and ILP.

There is no cluster example: open-source QuestDB is a single server. Primary-replica
replication (one writer, read replicas, through an object store) is a QuestDB Enterprise feature
([docs](https://questdb.com/docs/operations/replication/)), and there is no open-source
sharding or multi-primary mode. What the open-source clients do offer is client-side failover
across several addresses, which still needs replicated servers behind it.

## Benchmark

ILP over HTTP from 8 Python senders, then timed queries, 20M rows (6.7M trades + 13.3M quotes, 100 symbols, 3 days), server capped at 4 CPUs / 8 GB (Apple M4 Pro, Docker VM aarch64, 2026-10-02, shared VM, four runs): 2.2–3.1M rows/s acked into the WAL and 1.9–2.8M rows/s visible after the WAL apply, even with the senders' rows interleaved in time. One-hour interval aggregates, `count()` and `LATEST ON` answer in ~1 ms, 1-minute OHLCV for one symbol over 3 days in 17–29 ms, hourly VWAP for 100 symbols over all trades in 74–169 ms; `ASOF JOIN` trades→quotes takes 61–172 ms for an hour and 1.3–3.0 s for a full day (single-threaded). Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
