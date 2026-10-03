# Skytable

Website: https://skytable.io/

- [`single-node/`](single-node) — one `skyd` 0.8.4 (official multi-arch image) on Docker Compose with a root password, BlueQL through `skysh` (spaces, models, all data types, point DML, a standard user), and `sky-bench`.

There is no cluster example: Skytable 0.8.4 (August 2024) is the latest release and runs as a
single node only. It has no replication or clustering code, and no setting for either. The docs
call clustering and replication "on track" for "early Q1'25". The README says they arrive with
0.9, which is developed in a private repository. Its public `crv1` branch holds only empty
`cluster` module stubs (last commit January 2025), and 0.9 has not been released. A second `skyd`
would be an unrelated database.

There is no free or preview build with clustering either (checked 2026-10-03): no 0.9 release,
pre-release or nightly exists. The last update on the roadmap issue
([#203](https://github.com/skytable/skytable/issues/203), 2025-07-14) says clustering is
"complete… awaiting final integration" in a private fork; a release announced for July 2025 has not
shipped.

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
