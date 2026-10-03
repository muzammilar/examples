# QuestDB — market-data showcase (Rust client)

Live market-data ingestion on one QuestDB server while dashboard-style time-series SQL runs on
the newest seconds of data. [`showcase/`](showcase) is a Rust program: feed threads write quotes
and trades over ILP/HTTP with the official [`questdb-rs`](https://crates.io/crates/questdb-rs)
client, and PostgreSQL-wire connections ([`tokio-postgres`](https://crates.io/crates/tokio-postgres))
query at the same time. Host ports: web console `localhost:9001` (`QUESTDB_HTTP_PORT`), PG wire
`localhost:8813` (`QUESTDB_PG_PORT`).

```bash
make up      # start QuestDB (questdb/questdb:10.0.1)
make run     # build the showcase image (first time: about a minute) and run it
make status  # container state and server version
make down    # remove the containers, the data volume and the built image
```

What `make run` does (each run recreates its tables, so it can be repeated):

1. **tables**: `md_quotes` (bid/ask and sizes) and `md_trades` (side, price, qty, trade id),
   each with a designated timestamp, `PARTITION BY HOUR` and WAL. 50 symbols, each a random-walk
   mid price with a 1 bp spread. Every event writes a quote; one in three events also writes a
   trade, a buy at the ask or a sell at the bid. Rows carry the wall-clock time they were
   generated.
2. **live run, 1 feed**: one ILP/HTTP sender writes 500,000 rows/s (`RATE`) for 20 s
   (`DURATION`), 10,000 events per request (`BATCH`). Its timestamps only go forward, so every
   commit appends. Meanwhile 4 PG-wire clients (`QUERY_CLIENTS`) loop over six prepared queries
   on the newest data:
   - freshness: `SELECT ts FROM md_trades LIMIT -1` (the newest visible trade; its age is how
     long a trade takes to become queryable),
   - 1 s OHLCV bars for one symbol over the last minute (`SAMPLE BY 1s`),
   - VWAP and volume per symbol over the last minute,
   - 1 s mid-price bars for all symbols over the last 10 s,
   - top of book: `LATEST ON ts PARTITION BY symbol`,
   - execution quality over the last second: `md_trades ASOF JOIN md_quotes ON (symbol)`, the
     trade price against the mid of the quote in force, in basis points.
3. **live run, 4 feeds**: the same total rate from 4 senders, each owning a slice of the symbols.
   Their requests overlap in time, so the WAL apply has to merge out-of-order rows into the last
   partition.
4. After each run it waits until every acked row is visible and checks `count()` against what
   was sent. Then it prints the queries' results on the final data (bars, VWAP, top of book, and
   ASOF slippage: buys +0.51 bp, sells -0.51 bp, which matches the generator), a summary per run,
   and drops the tables (`KEEP=1` keeps the last run's). It exits non-zero on a count mismatch.

Override with `FEEDS` (feed counts to run one after the other, default `1,4`), `RATE`
(rows/s in total, `0` = as fast as possible), `DURATION`, `SYMBOLS`, `BATCH`, `QUERY_CLIENTS`,
`KEEP`, for example `make run RATE=0 FEEDS=1,4,8`.

- The [`showcase/Dockerfile`](showcase/Dockerfile) builds with `rust:1.98.1-trixie`
  (`questdb-rs` 7.0.0 needs Rust 1.91.1 or newer) and ships the binary on `debian:trixie-slim`.
  No host Rust needed. Only the crate's `sync-sender-http` feature is used, plus
  `ring-crypto` and `tls-webpki-certs`, which it requires even without TLS.
- `questdb-rs` 7.0 can also query, and send over QWP (QuestDB's binary protocol over WebSocket,
  server 10.0+). This showcase uses ILP/HTTP and PG wire, the interfaces most clients have.
- Compose project `questdb-market`, container `questdb-market`, so it runs next to
  [`../single-node`](../single-node).

## Sample output

2026-10-02, a fresh `make up` then `make run` (defaults), Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64), QuestDB 10.0.1, no CPU or memory caps. The Docker VM was
shared with other running containers.

```text
2. live, 1 feed (in time order): 10,000 events per ILP request, 500,000 rows/s in total + 4 PG-wire query clients, 20 s
      ingested  10,014,033 rows (7,510,000 quotes + 2,504,033 trades) in 20.0 s = 500,660 rows/s acked
                751 ILP requests, flush p50 8.5 ms / p99 36.6 ms; all rows visible 2 ms after the last ack
      check     count(): md_quotes 7,510,000 / 7,510,000 sent, md_trades 2,504,033 / 2,504,033 sent -> ok
      queries   7,387 during ingest (369/s, 0 errors); last trade visible p50 25 ms / p99 60 ms after its timestamp
        query                            runs  rows/run   p50 ms   p99 ms   max ms
        freshness (last trade)           1230         1     0.08     3.82    18.06
        ohlcv 1s, 1 symbol, 1m           1231         9     1.91    24.76    42.20
        vwap all symbols, 1m             1232        50     5.33    31.87    51.65
        mid 1s bars all, 10s             1232       354    28.49    70.15    99.62
        latest on (top of book)          1231        50     0.12     4.21    16.27
        asof join trades/quotes, 1s      1231       100    21.16    82.17   158.49
3. live, 4 feeds (interleaved, out of order): 10,000 events per ILP request, 500,000 rows/s in total + 4 PG-wire query clients, 20 s
      ingested  10,029,136 rows (7,520,000 quotes + 2,509,136 trades) in 20.0 s = 501,422 rows/s acked
                752 ILP requests, flush p50 7.6 ms / p99 31.1 ms; all rows visible 3 ms after the last ack
      check     count(): md_quotes 7,520,000 / 7,520,000 sent, md_trades 2,509,136 / 2,509,136 sent -> ok
      queries   3,256 during ingest (163/s, 0 errors); last trade visible p50 65 ms / p99 233 ms after its timestamp
        query                            runs  rows/run   p50 ms   p99 ms   max ms
        freshness (last trade)            541         1     0.06    11.94    36.91
        ohlcv 1s, 1 symbol, 1m            542         8     0.73    15.68    26.40
        vwap all symbols, 1m              543        50     2.73    26.83    32.25
        mid 1s bars all, 10s              544       288    14.02    49.02    79.52
        latest on (top of book)           543        50     0.15    14.16    45.65
        asof join trades/quotes, 1s       543       100    21.29  2589.99  6156.51
4. results      the same SQL on the last run's data (time windows end at the last tick)
    BTC-USD 1 s OHLCV, last 5 bars:
                  ts          open          high           low         close        volume        trades
        06:43:04.000      64937.32      64952.72      64913.36      64918.75      23176.64          2349
        06:43:05.000      64924.97      64932.42      64903.28      64924.23      26114.63          2606
        06:43:06.000      64924.65      64933.14      64909.33      64912.21      23224.41          2282
        06:43:07.000      64912.50      64926.27      64887.17      64897.80      23191.09          2331
        06:43:08.000      64904.32      64911.62      64892.82      64899.78       8016.27           787
    ASOF JOIN, last 1 s: trade price vs. quote mid in bp (buys lift the ask: ~+0.5, sells hit the bid: ~-0.5):
                side        trades       avg_bps
                 buy         66606       0.51327
                sell         66668      -0.51308
5. summary
  feeds        rows/s   queries/s   fresh p50   fresh p99   asof p50 ms   asof p99 ms
      1       500,660         369          25          60          21.2          82.2
      4       501,422         163          65         233          21.3        2590.0

SHOWCASE PASSED
```

(Per-second progress lines and the VWAP / top-of-book tables trimmed.)

Runs on the same machine, 20 s each (`fresh` = age of the newest visible trade when the
freshness query returns, ms; `asof` = the 1 s ASOF JOIN, ms):

| run | feeds | rows/s acked | queries/s | fresh p50 / p99 | asof p50 / p99 / max |
|-----|------:|-------------:|----------:|----------------:|---------------------:|
| `make run` (500k/s) | 1 | 500,660 | 369 | 25 / 60 | 21 / 82 / 158 |
| | 4 | 501,422 | 163 | 65 / 233 | 21 / 2,590 / 6,157 |
| `RATE=500000`, earlier run | 1 | 501,325 | 281 | 29 / 95 | 25 / 94 / 146 |
| | 4 | 501,433 | 66 | 76 / 382 | 35 / 6,520 / 10,256 |
| `RATE=1000000` | 1 | 886,615 (fell behind after 10 s) | 208 | 19 / 77 | 43 / 116 / 195 |
| | 4 | 1,002,630 | 39 | 47 / 273 | 59 / 8,614 / 13,673 |
| `RATE=0 FEEDS=1,4,8` (unthrottled) | 1 | 905,310 | 165 | 20 / 85 | 49 / 168 / 292 |
| | 4 | 1,992,399 | 4 | 78 / 194 | 4,347 / 21,980 / 21,980 |
| | 8 | 2,338,445 | 33 | 4,553 / 7,672 | windows mostly empty (data lagged); one query hit the 60 s timeout |

What the numbers show:

- **Fresh data is queryable within tens of milliseconds.** With one in-order feed at 500k
  rows/s, the newest trade is visible 25 ms (p50) after it was generated, including the batch
  fill time on the client. Acked rows are visible 2–3 ms after the last ack.
- **Time-bounded queries stay cheap as the table grows.** The designated timestamp turns
  `ts > dateadd('m', -1, now())` into a scan of the newest rows only, and `LATEST ON` reads from
  the end of the table: 0.1 ms for top of book and 2–5 ms for 1-minute bars and VWAP, 369 queries/s
  from 4 clients, all while 10M rows were written.
- **ASOF JOIN does the market-data join in SQL.** Matching every trade with the quote in force
  at its timestamp, per symbol, takes ~21 ms for a second of data (~125k trades vs. ~375k quotes).
- **In-order ingest is the fast path.** One sender writing in time order appends. With 4
  senders at the same total rate, their batches overlap in time, so the WAL apply has to merge
  out-of-order rows into the last partition (the server log shows `o3 partition task` and
  partition splits). Ingest keeps up, but query throughput halves and the ASOF JOIN p99 goes from
  ~80 ms to seconds. Unthrottled, 4 senders reach ~2M rows/s and 8 reach 2.3M rows/s, but at 8
  the visible data lags 4–8 s behind and an ASOF query hit the 60 s query timeout. One sender tops
  out around 0.9M rows/s (synchronous HTTP flushes from one thread). For live dashboards, keep
  each table's writes in time order (one writer per table) and size the rate to what one
  in-order writer can do.
