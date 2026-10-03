# kdb+

Website: https://kx.com/ (docs: https://code.kx.com/q/)

kdb+ is KX's columnar, in-memory and on-disk time-series database and the q language in one
~1 MB binary. The examples run kdb+ 4.1 (`q`, build 2026.10.02) in Docker; its q code is plain
kdb+ q and also runs on KDB-X, KX's newer release of kdb+ (`q` 5.0).

- [`single-node/`](single-node) — one q process on Docker Compose: in-memory tables, q-sql
  (`select`/`exec`/`update`/`by`, `xbar` OHLC bars, `lj`), as-of join `aj`, window join `wj`,
  the `s#` `g#` `p#` attributes with timings, and splayed and date-partitioned tables on disk.
- [`capital-markets/`](capital-markets) — tick analytics from a C++ client (KX C API, `k.h` +
  `c.o`): ~60M simulated trades and quotes streamed over IPC as column batches, then VWAP, OHLCV
  bars, `aj` of every trade to its quote, trade classification, effective spread, realized
  volatility and `wj` on the server, checked against the client's own counts.

**Why there is no cluster example.** kdb+ has no built-in replication, sharding or consensus:
each q process is a single-node database. Its standard multi-process deployment is the
[tick architecture](https://code.kx.com/q/architecture/) (tickerplant, real-time and historical
databases, a gateway), built by hand in q. KX's paid
[kdb Insights Enterprise](https://code.kx.com/insights/enterprise/) is the distributed product (free
trial only), and the free Community Edition license covers one machine only
([usage restrictions](https://code.kx.com/licensing/usage-restrictions.html)).

## License: required before running

**q does not start without a license key** (`license error: k4.lic`). Use the free
**KDB-X Community Edition** key:

1. Sign up (name, email) at https://developer.kx.com/products/kdb-x/install. The welcome email
   contains the key as a base64 string (`kc.lic`). Free for personal and commercial use, no
   expiry, runs offline; limits: 16 GB RAM for q, 24 cores, 4 secondary threads and 16
   connections per q process, one machine.
2. Put it in **`kdb/license/kc.lic`** (gitignored), or export it:

   ```bash
   base64 -d <<<'<the base64 string from the email>' > kdb/license/kc.lic
   # or
   export KDB_LICENSE_B64='<the base64 string from the email>'   # or put it in the repo-root .env
   ```

3. `cd kdb/single-node && make up && make test && make benchmark`, or
   `cd kdb/capital-markets && make up && make run`. `make up` refuses to start without a key.

The container's entrypoint copies the key into `$QLIC` (`/tmp/kx`) at start-up; it never goes into
an image layer. A commercial `k4.lic` also works (`kdb/license/k4.lic` or `KDB_LICENSE_K4B64`).

The image ([`image/`](image)) downloads the pinned kdb+ 4.1 zip from KX's portal (no login) and
checks its sha256. The Community Edition `kc.lic` is a newer format, and whether kdb+ 4.1 accepts
it is not verified. If q 4.1 rejects it with a `license error`, run the KDB-X 5.0 binary instead:
`KDB_DIST=kdb-x make up`. The q code is the same.

## Benchmark

Not run: no license key was available (2026-10-02, Apple M4 Pro, Docker VM aarch64). What was
checked: the image builds for linux/arm64 with the pinned kdb+ 4.1 `l64arm` zip (sha256 verified),
q starts and exits with `license error: k4.lic`, `make up` refuses to start without a key, and the
C++ client compiles against KX's `c.o` for arm64. With a key, `make benchmark` in
[`single-node/`](single-node#benchmark) measures generation, IPC ingest, q-sql aggregations, `aj`,
`wj` and partition writes over 10M trades and 20M quotes (server capped at 4 CPUs / 6 GiB), and
`make run` in [`capital-markets/`](capital-markets) prints its own timings.

## Known issues

- Not run yet: the q code (walkthrough, benchmark, the C++ client's queries) was written without
  a license, so expect fixes on the first licensed run.
- The only free key (KDB-X Community Edition) needs a sign-up. The old kdb+ Personal Edition and
  the free 32-bit builds are gone, and PyKX without a license cannot run q.
- Not verified whether kdb+ 4.1 accepts a KDB-X Community Edition `kc.lic`; `KDB_DIST=kdb-x` is
  the fallback.
- The Rust `kdbplus` crate's last release was 0.3.9 in 2024, so the client uses KX's C API
  (`k.h` + `c.o`) from C++.
