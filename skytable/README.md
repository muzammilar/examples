# Skytable

Website: https://skytable.io/

- [`single-node/`](single-node) — one `skyd` 0.8.4 (official multi-arch image) on Docker Compose with a root password, BlueQL through `skysh` (spaces, models, all data types, point DML, a standard user), and `sky-bench`.
- [`session-store-showcase/`](session-store-showcase) — an API gateway's session store and per-key rate limiter in Rust (official `skytable` driver): typed session rows; in-place `hits += 1` / `tokens -= 1` that stay exact under 32 concurrent clients, where read-modify-write loses 94%; pipelining against one query per round trip; and the driver's 41 ms Nagle stall on small pipelines.

There is no cluster example: Skytable 0.8.4 (August 2024) is the latest release and runs as a
single node only. It has no replication or clustering code, and no setting for either. The docs
call clustering and replication "on track" for "early Q1'25". The README says they arrive with
0.9, which is developed in a private repository. Its public `crv1` branch holds only empty
`cluster` module stubs (last commit January 2025), and 0.9 has not been released. A second `skyd`
would be an unrelated database.

## Benchmark

`sky-bench` `uniform_std_v1`, 1M rows (4M queries), one server capped at 4 CPUs / 4 GB, 32
connections (Apple M4 Pro, Docker VM aarch64, 2026-10-02): ~185–210k INSERT/UPDATE/SELECT/DELETE
per second at ~0.13 ms p50 and ~0.6–1.1 ms p99. Each query is its own round trip. skyd used
under 2 CPUs, and 8 CPUs did not go faster. Full table and method:
[`single-node/README.md`](single-node/README.md#benchmark).

## Known issues

Skytable is not stable enough to depend on yet: 0.8.4 (August 2024) is still the latest release,
clustering promised for early 2025 has not shipped, and 0.9 is developed in a private repository.
Seen while building these examples (2026-10-02):

- The `skytable/skytable:v0.8.4` release image is amd64 only, so on Apple silicon it runs under
  emulation. These examples pin a multi-arch build of the `next` branch (still reports 0.8.4).
- skysh cannot send signed integers (`-5`, `-=`) and only parses a float when punctuation follows
  it. Details: [`single-node/README.md`](single-node/README.md).
- After `ALTER MODEL ... ADD` on a model with rows, `SELECT *` on an old row returns error `101`
  and `SELECT ALL` panics a server task (`sel.rs:108`). skyd keeps running.
- The official Rust driver (`skytable` 0.8.12) leaves Nagle's algorithm on and writes a pipeline
  in two pieces, so every small pipeline stalls ~41 ms. The driver doesn't expose its socket; the
  showcase sets `TCP_NODELAY` itself. Details:
  [`session-store-showcase/README.md`](session-store-showcase/README.md).
