# KDB-X (kdb+)

Website: https://kx.com/ (KDB-X: https://developer.kx.com/products/kdb-x)

KDB-X is the current release of KX's kdb+: a columnar, in-memory and on-disk time-series database
and the q language in one ~1 MB binary. The directory is `kdb-x/` after the product these examples
run (`q` 5.0 from the KDB-X download); the q code is plain kdb+ q.

- [`single-node/`](single-node) — one q process on Docker Compose: in-memory tables, q-sql
  (`select`/`exec`/`update`/`by`, `xbar` OHLC bars, `lj`), as-of join `aj`, window join `wj`,
  the `s#` `g#` `p#` attributes with timings, and splayed and date-partitioned tables on disk.
- [`capital-markets-showcase/`](capital-markets-showcase) — tick analytics from a C++ client
  (KX C API, `k.h` + `c.o`): ~60M simulated trades and quotes streamed over IPC as column batches,
  then VWAP, OHLCV bars, `aj` of every trade to its quote, trade classification, effective spread,
  realized volatility and `wj` on the server, checked against the client's own counts.

## License

The `q` binary is a public download (the shared image, [`image/`](image), fetches the pinned
`5.0.20261002` build for linux arm64 or amd64 from `portal.dl.kx.com` and checks its sha256), but
**q will not start without a license** (`license error: no license loaded`). KDB-X Community
Edition is free for personal and commercial use; you get its key by signing up at
https://developer.kx.com/ (name and email), which sends a welcome email with the base64-encoded
`kc.lic`. Community Edition limits: 16 GB of RAM for q, 24 cores, 4 secondary threads per process,
16 connections per process, one machine. Give it to the examples in one of two ways:

```bash
export KDB_LICENSE_B64='<the base64 string from the email>'   # or put it in the repo-root .env
# or
base64 -d <<<'<the base64 string>' > kdb-x/license/kc.lic       # gitignored
```

A commercial `k4.lic` works the same way (`KDB_LICENSE_K4B64` or `kdb-x/license/k4.lic`). The
container's entrypoint writes the license into `$QLIC` (`/tmp/kx`) at start-up; it never goes
into an image layer. `make up` refuses to start without one.

## Benchmark

Not run yet: these examples were written without a license key, so the q code has not been
executed. The image builds and starts (Apple M4 Pro, Docker VM aarch64, 2026-10-02) and stops at
the license check. With a license, `make benchmark` in [`single-node/`](single-node#benchmark)
measures generation, IPC ingest, q-sql aggregations, `aj`, `wj` and partition writes over 10M
trades and 20M quotes; [`capital-markets-showcase/`](capital-markets-showcase) prints its own
timings.
