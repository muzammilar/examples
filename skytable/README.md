# Skytable

Website: https://skytable.io/

| folder | what |
|--------|------|
| [`single-node/`](single-node) | One `skyd` 0.8.4 (official multi-arch image) on Docker Compose with a root password; BlueQL through `skysh` (spaces, models, all data types, point DML, a standard user); `sky-bench`. |
| [`session-store/`](session-store) | API gateway session store and per-key rate limiter in Rust (official `skytable` driver): typed session rows; in-place `hits += 1` / `tokens -= 1` that stay exact under 32 concurrent clients (read-modify-write loses 94%); pipelining vs one query per round trip; the driver's 41 ms Nagle stall on small pipelines. |

## No cluster example

Skytable 0.8.4 (August 2024) is the latest release and runs as a single node only: no replication
or clustering code, no setting for either. A second `skyd` would be an unrelated database.

- The docs call clustering and replication "on track" for "early Q1'25". The README says they
  arrive with 0.9, developed in a private repository. The public `crv1` branch holds only empty
  `cluster` module stubs (last commit January 2025).
- No 0.9 release, pre-release or nightly exists (checked 2026-10-03). The last update on the
  roadmap issue ([#203](https://github.com/skytable/skytable/issues/203), 2025-07-14) says
  clustering is "complete… awaiting final integration" in a private fork; a release announced for
  July 2025 has not shipped.

## Benchmark

Apple M4 Pro, Docker VM aarch64.

| example | date | workload | result |
|---------|------|----------|--------|
| [single-node](single-node/README.md#benchmark) | 2026-10-02 | `sky-bench` `uniform_std_v1`, 1M rows (4M queries), server 4 CPUs / 4 GB, 32 connections, one round trip per query | ~185–210k INSERT/UPDATE/SELECT/DELETE per second, ~0.13 ms p50, ~0.6–1.1 ms p99. skyd used under 2 CPUs; 8 CPUs was not faster. |
| [session-store](session-store/README.md#sample-output) | 2026-10-03 | Rust driver, server and client 4 CPUs each, min–max of three runs, idle VM | Point reads ~1.9–2.1M queries/s pipelined (64 connections × depth 16) vs ~330–470k unpipelined; single-connection inserts 33–41x faster pipelined; 4-query request path 2.6–3.2x faster. Server-side `n += 1` exact at 32,000 increments; client-side read-modify-write lost 94%. Driver default (Nagle on): ~41 ms per small pipeline (~400 queries/s on one connection). |

## Known issues

Not stable enough to depend on yet: 0.8.4 (August 2024) is still the latest release, clustering
promised for early 2025 has not shipped, and 0.9 is developed privately. Seen 2026-10-02:

- `skytable/skytable:v0.8.4` is amd64 only (emulated on Apple silicon). These examples pin a
  multi-arch build of the `next` branch (still reports 0.8.4).
- skysh cannot send signed integers (`-5`, `-=`) and only parses a float when punctuation follows
  it. See [`single-node/README.md`](single-node/README.md#known-issues).
- After `ALTER MODEL ... ADD` on a model with rows, `SELECT *` on an old row returns error `101`
  and `SELECT ALL` panics a server task (`sel.rs:108`). skyd keeps running.
- The Rust driver (`skytable` 0.8.12) leaves Nagle's algorithm on and writes a pipeline in two
  pieces, so every small pipeline stalls ~41 ms. The driver doesn't expose its socket; the
  example sets `TCP_NODELAY` itself. See
  [`session-store/README.md`](session-store/README.md#the-driver-and-tcp_nodelay).
