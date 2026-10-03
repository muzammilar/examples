# kdb+ — capital markets (C++ client)

Tick data analytics, the workload kdb+ was built for, on one q process driven by
[`app/main.cpp`](app/main.cpp): a C++ program using KX's official C API (`k.h` + `c.o`).
The server is the shared [`../image`](../image) (kdb+ 4.1, `q -p 5000 -s 4`, host port `5102`,
override with `KDB_PORT`) and needs a kdb+ license key (free: KDB-X Community Edition), see
[`../README.md#license`](../README.md#license); the client does not.

```bash
make up      # build the image and start q
make build   # build the C++ client image (k.h + c.o, g++)
make run     # run the analytics (TRADES=20000000 QUOTES_PER_TRADE=2 SYMS=100 BATCH=1000000)
make status  # container state
make down    # remove the containers and the built images
```

What `make run` does:

1. **Simulate and load.** One trading day (2026.10.02 09:30-16:00) for `SYMS` symbols with
   Zipf-like activity: an opening quote per symbol, then a stream of about `TRADES` trades and
   `TRADES × QUOTES_PER_TRADE` quotes (default ~60M rows) with strictly increasing nanosecond
   timestamps. A quote moves the symbol's mid (random walk on a half-cent grid) and sets a 1-5
   cent spread; a trade prints at the current ask (45%, buy), bid (45%, sell) or mid (10%). The
   client fills column vectors (`ktn` of `KP`, `KS` via `ss`, `KF`, `KJ`) and sends each
   `BATCH`-row table (`xT(xD(names, columns))`) with `k(h, "insert", ...)`. Generation and IPC
   insert are timed separately.
2. **Analytics on the server**, each a q expression timed at the client (round trip):
   `g#` on the quote `sym`; count, volume, notional and VWAP by sym; one-minute OHLCV bars for
   every symbol (`xbar time.minute`); five-minute VWAP bars; **`aj` of every trade to the
   prevailing quote**; buy/sell/mid classification from the joined bid/ask; effective spread in
   bps; realized volatility from the one-minute closes (`dev 1_ log ratios close`); a **`wj`**
   (max ask, min bid, quote count in the 1 s before each trade) for the busiest symbol; and a
   full-column vector scan (`exec sum price*size`). It prints samples of each result through
   `.Q.s` and the server's memory.
3. **Verify.** The client kept per-symbol counts, volume, notional and how many trades it put at
   the ask, the bid and the mid. q's `by sym` results must match exactly (notional to 1e-9), and
   the classification from the `aj` must reproduce the buy/sell/mid counts exactly, which only
   holds if every trade got the right quote. Exits 1 with `VERIFY FAILED` otherwise.

Each run drops and recreates the tables. Memory: the default (~20M trades, ~40M quotes) needs
about 4-5 GB in q including the `aj` result; the KDB-X Community Edition key caps q at 16 GB,
which allows about `TRADES=50000000` (~150M rows).

## The C++ client

The C API is two files from [github.com/KxSystems/kdb](https://github.com/KxSystems/kdb) (Apache
2.0): `c/c/k.h` and the prebuilt object `l64arm/c.o` (or `l64/c.o` on amd64), pinned to commit
`3af0d47` with sha256 checks in [`app/Dockerfile`](app/Dockerfile), which compiles
`main.cpp` with Debian's g++ (`-std=c++20`, `-lpthread`, nothing else) and copies the 80 KB binary
into `debian:trixie-slim`. It connects with `khpunc(..., capability 1)` so a message may exceed
2 GB. `k.h` defines one-letter macros (`O`, `R`, `U`, `Z`, ...), which `main.cpp` undefines after
the include. C++ over the C API was chosen over Rust: KX maintains `c.o` for linux arm64, while the
`kdbplus` crate's last release is 0.3.9 (2024).

## Sample output

Not run: no license key was available (2026-10-02, Apple M4 Pro, Docker VM aarch64). The client
compiles and links against `c.o` on arm64 without warnings and exits with `cannot connect` when
no server answers; the kdb+ 4.1 server stops at the license check (`license error: k4.lic`).

## Why kdb+ fits this

- **Columns in memory, vector operations.** A table is a set of typed arrays; `sum price*size`
  over tens of millions of rows is one pass over two contiguous float/long vectors, and every
  q-sql aggregate works the same way.
- **Time-series joins are primitives.** `aj` (as-of) and `wj` (window) join a trade stream to a
  quote stream by symbol and time; with `g#`/`p#` on `sym` they are binary searches within each
  symbol's rows, not the range joins a row store needs.
- **Bars are a group-by.** `xbar` buckets timestamps, so OHLCV or VWAP bars at any interval are
  one `select ... by sym, n xbar time.minute`.
- **Bulk IPC.** A client sends whole columns in one message; q appends them to the table without
  per-row work.
