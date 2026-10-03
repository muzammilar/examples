# kdb+

Website: https://kx.com/ (docs: https://code.kx.com/q/)

kdb+ is KX's columnar, in-memory and on-disk time-series database and the q language in one
~1 MB binary. These examples run kdb+ 4.1 (`q`, build 2026.10.02) in Docker; the q code is plain
kdb+ q and also runs on KDB-X, KX's newer release of kdb+ (`q` 5.0).

- [`single-node/`](single-node) — one q process on Docker Compose: in-memory tables, q-sql
  (`select`/`exec`/`update`/`by`, `xbar` OHLC bars, `lj`), as-of join `aj`, window join `wj`,
  the `s#` `g#` `p#` attributes with timings, and splayed and date-partitioned tables on disk.
- [`capital-markets-showcase/`](capital-markets-showcase) — tick analytics from a C++ client
  (KX C API, `k.h` + `c.o`): ~60M simulated trades and quotes streamed over IPC as column batches,
  then VWAP, OHLCV bars, `aj` of every trade to its quote, trade classification, effective spread,
  realized volatility and `wj` on the server, checked against the client's own counts.

**Why there is no cluster example.** kdb+ has no built-in replication, sharding or consensus:
each q process is a single-node database. Its standard multi-process deployment is the
[tick architecture](https://code.kx.com/q/architecture/): a tickerplant logs every update to disk
and publishes it to a real-time database (RDB, today's data in memory) and other subscribers, the
RDB writes to a date-partitioned historical database (HDB) at end of day, and a
[gateway](https://code.kx.com/q/wp/query-routing/) routes queries across them. Scale and
resilience come from running more RDB/HDB processes behind the gateway and from
[replaying the tickerplant log](https://code.kx.com/q/wp/data-recovery/) after a restart, all
built by hand in q. KX's paid [kdb Insights Enterprise](https://code.kx.com/insights/enterprise/)
packages that into a Kubernetes platform (stream processor, storage manager, data access
processes, scaling, fault tolerance, entitlements, backup/restore); it has a free trial, no free
edition. The free KDB-X Community Edition license also only covers one machine
([usage restrictions](https://code.kx.com/licensing/usage-restrictions.html)), so a multi-host
deployment is outside its terms; several q processes on one host (as in Docker Compose here) are
fine.

## License

**Every kdb+ q needs a license key, and today the only free one needs a sign-up.** The kdb+
binary itself is a public download: KX's portal serves the kdb+ 4.1 zips
(`https://portal.dl.kx.com/assets/raw/kdb+/4.1/<build>/l64arm.zip`, also `l64`, `m64`, `l32`)
without a login (checked 2026-10-03, although [the install guide](https://code.kx.com/q/learn/install/)
calls it the commercial customers' portal). The shared image, [`image/`](image), fetches the
pinned `4.1/2026.10.02` build for linux arm64 or amd64 and checks its sha256. Without a key, q
prints `license error: k4.lic` and exits; that is true of every build on the portal, including
the 32-bit `l32` one (checked under linux/386 emulation).

What there is ([licensing](https://code.kx.com/q/learn/licensing/)):

| | what | how | limits |
| --- | --- | --- | --- |
| kdb+ Personal Edition (64-bit, `kc.lic` "on demand") | the old free kdb+ key | gone: `kx.com/kdb-personal-edition-download/` and `ondemand.kx.com` now redirect to KDB-X | was non-commercial only, 16 cores, and needed an always-on internet connection to KX's licensing servers |
| kdb+ 32-bit | the older free build that needed no key | withdrawn; the current `l32` build needs a key too | — |
| **KDB-X Community Edition** (`kc.lic`) | the free key KX offers now, "the next evolution of kdb+" ([install guide](https://code.kx.com/q/learn/install/)) | sign up (name, email) at https://developer.kx.com/products/kdb-x/install; the welcome email has the base64 `kc.lic` | free for personal **and** commercial use, no expiry, runs offline; 16 GB RAM for q, 24 cores, 4 secondary threads and 16 connections per q process, one physical or virtual machine ([usage restrictions](https://code.kx.com/licensing/usage-restrictions.html)) |
| commercial kdb+ (`k4.lic`) | a paid kdb+ license | sales@kx.com; tied to the licensed host name | per contract |
| kdb Insights Enterprise | the distributed platform | free trial only | — |

**Which to pick:** a KDB-X Community Edition key unless you already have a commercial `k4.lic`.
The images build kdb+ 4.1 by default. The Community Edition `kc.lic` is a newer format (KDB-X
added "the new community `kc.lic` license with embedded resource limits",
[release notes](https://code.kx.com/kdb-x/releases/release-notes-latest.html)); whether kdb+ 4.1
accepts it is not verified here (no key was available). If q 4.1 rejects it with a
`license error`, build the same image with the KDB-X binary instead: `KDB_DIST=kdb-x make up`
(the pinned `5.0.20261002` zip, sha256-checked); the examples and q code are the same.

Give the key to the examples in one of two ways:

```bash
export KDB_LICENSE_B64='<the base64 string from the email>'   # kc.lic; or put it in the repo-root .env
export KDB_LICENSE_K4B64="$(base64 < k4.lic)"                  # or a commercial k4.lic
# or
base64 -d <<<'<the base64 string>' > kdb/license/kc.lic         # or k4.lic; gitignored
```

The container's entrypoint writes the license into `$QLIC` (`/tmp/kx`) at start-up; it never goes
into an image layer. `make up` refuses to start without one. A commercial `k4.lic` is bound to a
host name: set `hostname:` on the compose services to match it.

## Benchmark

Not run: no license key was available (2026-10-02, Apple M4 Pro, Docker VM aarch64, 11 CPUs,
24 GiB). What was checked: the image builds for linux/arm64 with the pinned kdb+ 4.1 2026.10.02
`l64arm` zip (sha256 verified), q starts and exits with `license error: k4.lic`, and the
entrypoint refuses to start without a key; the showcase's C++ client compiles against KX's
`c.o` for arm64. With a key, `make benchmark` in
[`single-node/`](single-node#benchmark) measures generation, IPC ingest, q-sql aggregations,
`aj`, `wj` and partition writes over 10M trades and 20M quotes, with the server capped at
4 CPUs / 6 GiB; [`capital-markets-showcase/`](capital-markets-showcase) prints its own timings.

## Known issues

- Nothing here has run yet. The q code (walkthrough, benchmarks, tick gateway and feed, the
  C++ showcase's queries) was written without a license, so expect fixes on the first licensed
  run.
- q needs a license key even for the free edition, and the only free key (KDB-X Community
  Edition) comes by signing up. The old kdb+ Personal Edition and free 32-bit builds are gone,
  and PyKX without a license cannot run q.
- Not verified whether kdb+ 4.1 accepts a KDB-X Community Edition `kc.lic`; `KDB_DIST=kdb-x`
  is the fallback.
- The Rust `kdbplus` crate's last release was 0.3.9 in 2024, so the showcase uses KX's C API
  (`k.h` + `c.o`) from C++ instead.
