# kdb+ — single node

One q process (`q -p 5000 -s 4`) from the shared [`../image`](../image) (Debian slim + the
public kdb+ 4.1 `q` binary, build 2026.10.02, arm64 or amd64; `KDB_DIST=kdb-x` for KDB-X q 5.0),
IPC on `localhost:5101` (override with `KDB_PORT`), data in the `kdb-data` volume at `/data`.

## Quick start

**License: required before running.** Put a key (free: KDB-X Community Edition) in
`kdb/license/kc.lic`, see [`../README.md#license`](../README.md#license-required-before-running);
`make up` refuses to start without one.

```bash
make up         # build the image, start q, wait for the port
make test       # q/walkthrough.q through q/run.q (see below)
make status     # container state; version, threads, memory and tables via IPC
make cli        # a q console in the container (h:hopen 5000; h"tables[]" talks to the server)
make benchmark  # bench/bench.q against the server: 10M trades, 20M quotes (SMOKE=1: 1M)
make down       # remove the container, the data volume and the built image
```

`make test` runs a second q process in the server's container ([`q/run.q`](q/run.q)) that sends
[`q/walkthrough.q`](q/walkthrough.q) to the server one line at a time over IPC and prints each
line as `q)line` with its result, like a console session (assignments print nothing). It stops at
the first error. The walkthrough:

1. **Tables**: one day of seeded trades (1M rows) and quotes (2M) for five symbols, `meta`, a
   keyed reference table and `upsert` by key.
2. **q-sql**: `select ... where` with `within`, `by sym` aggregates (`count`, `sum`, `wavg`
   VWAP), `exec`, one-minute OHLC bars with `xbar`, `update` in place, `lj` with the keyed table.
3. **`aj`**: the prevailing quote for each trade, timed with `\t` before and after `g#` on the
   quote `sym` column; buyer- and seller-initiated counts from the joined bid/ask.
4. **`wj`**: max ask, min bid and quote count in the 2 s before each AAPL trade, over the quotes
   sorted by `sym`,`time` with `p#sym`.
5. **Attributes**: `attr` of each column; `within` on a 10M-row column with and without `s#`, a
   `sym=` filter on 10M rows / 17,576 symbols with and without `g#`; `p#` after `xasc`.
6. **Splayed**: `set` of the reference table as `/data/db/ref/` (one file per column, `.d` for
   the column order) after `.Q.en` enumerates its symbols into `/data/db/sym`.
7. **Partitioned**: three dates written with `.Q.dpft` (sorted by `sym`, `p#sym`) as
   `/data/db/<date>/trades/`, then `\l /data/db` maps the database: `meta`, rows and VWAP by the
   virtual `date` column, and queries that prune by date first.

The server keeps its state between runs (in-memory tables, `/data/db`, and `/data/db` as its
working directory after the `\l`); re-running `make test` rewrites the same tables and partitions.

## Benchmark

`make benchmark` (after `make up`) runs [`bench/bench.q`](bench/bench.q) in a separate q
container (`bench` service, same image, `cpus: 2`). It defines its functions on the server over
IPC and times the work there with `.z.n`, so the numbers are server time; only the IPC ingest and
round trip are timed at the client. `N` trades (default 10M; `SMOKE=1`: 1M; or `make benchmark
N=...`) and `2N` quotes over 100 symbols:

| metric | what |
| --- | --- |
| `generate_trades_and_quotes` | build both tables in memory, sorted by time (3N rows) |
| `ipc_insert_sync_100k_batches` / `_async_` | client sends N rows as 100k-row tables, `insert` on the server |
| `ipc_roundtrip_10k_queries` | 10k sync `h"1+1"` from one client: QPS, p50/p99 |
| `select_by_sym_count_sum_vwap` | `count`, `sum size`, `size wavg price` by sym over N trades |
| `ohlc_1min_bars_all_syms` | first/max/min/last/sum by sym and `1 xbar time.minute` |
| `vector_sum_price_x_size` | `exec sum price*size` |
| `filter_one_sym_no_attr` / `_g_attr` | ``select from trade where sym=`S7``, before and after `g#sym` |
| `aj_trades_to_quotes` | ``aj[`sym`time;trade;quote]`` over N trades and 2N quotes (`g#sym`) |
| `wj_1s_window_1m_trades` | `wj` max ask / min bid / count in a 1 s window for the first 1M trades |
| `write_partition_dpft` | `.Q.dpft` of the N trades as one date partition under `/data/bench` |
| `hdb_vwap_by_sym_one_date` / `hdb_one_sym_one_date_p_attr` | the same queries on the mapped partition |

Results print as a table and go to `results/kdb-single-<UTC time>.json` (git-ignored) with the
server version, thread count, memory used and the limits below. Afterwards the server drops the
benchmark tables from memory but keeps `/data/bench` loaded (its working directory) until the
next `\l`, so `make test` after a benchmark re-loads `/data/db`.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `kdb` container
at `BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update`, and restores the old limits
afterwards (an unlimited memory limit comes back as the Docker VM's total; `make down && make up`
starts clean). q runs with `-s 4` secondary threads, the KDB-X Community Edition maximum. The JSON
records the applied limits under `limits`.

### Sample results

Not run: no license key was available (2026-10-02, Apple M4 Pro, Docker VM aarch64). The image
builds with the pinned kdb+ 4.1 2026.10.02 `l64arm` zip, and q starts and exits at the license
check (`license error: k4.lic`); the walkthrough and the benchmark have not been executed yet.
