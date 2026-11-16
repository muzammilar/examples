# Neon: storage/compute separation

Website: [neon.com](https://neon.com/)
GitHub: [neondatabase/neon](https://github.com/neondatabase/neon)

Neon stores PostgreSQL WAL in safekeepers, serves database pages through a Rust
pageserver and can start a compute endpoint on an ancestor timeline. These
self-hosted examples measure the native mechanisms and their recovery boundaries.
The pinned snapshot uses PostgreSQL 17.5, emergency control-plane mode and one
pageserver. It does not reproduce managed Neon's HA/control plane or establish a
current patched production deployment.

Each example exposes `orders-workload/` through a tracked source symlink. Its
Makefile materializes an ignored `.build/` Docker context before building.
Configuration, dependency definitions and controllers are local to the example;
its lifecycle does not depend on running a sibling example.

| Example | What it demonstrates | Validation |
|---|---|---|
| [Minimal single-copy stack](single-node/) | Compute + pageserver + broker + one safekeeper; local-filesystem backend, plain PostgreSQL baseline | Million rows per backend, complete bodies, query timings and full cleanup passed; no HA claim |
| [Clustered branching/recovery](branching-recovery/) | Three WAL copies, Garage S3, native timeline branch, PostgreSQL three-copy baseline, compute/WAL failure and empty-disk recovery | Million rows per backend/all PostgreSQL copies; 133,600 recovery ACKs across five live cases, zero loss or partial COPY transactions; full post-recovery branch readback and cleanup passed; earlier failed restart retained in archived evidence |
| [Kubernetes StatefulSets](kubernetes-statefulset/) | Dedicated kind cluster, native storage/compute components; fallback rather than an official Neon operator | Million-row parent/branch and PostgreSQL-copy checks, five live recovery cases; 300,400 historical ACKs, zero loss/partial transactions; full cleanup passed |

## Benchmark summary

2026-11-16 for single-node/Compose; 2026-11-15 for Kubernetes. Apple M4 Pro, native aarch64 Docker, shared 11-CPU / 24.42 GiB VM.
Budgets and caveats differ between shapes; each README records exact limits.

| Example | Neon / PostgreSQL COPY rate | Branch/recovery observation |
|---|---|---|
| [Single copy](single-node/#measured-results) | 341,647 / 524,060 rows/s | One million rows per backend; zero missing/incorrect values |
| [Clustered](branching-recovery/#measured-results) | 301,935 / 375,534 rows/s | Branch API 118.083 ms; usable branch SQL including startup 6.601 s; full logical PostgreSQL copy 4.064 s, different operations |

The branch API was inexpensive, while endpoint startup dominated its usable time.
Do not compare API-only creation with a complete logical restore as a speed ratio.
The example verifies ancestor-LSN contents and branch isolation; it does not
measure shared-layer storage savings or large-branch scaling.

## Known issues

An intermittent compute restart failed on a retained Unix-socket lock after
SIGKILL. Ephemeral `/tmp` fixed the tested restart path without resetting durable
data. PostgreSQL bootstrap needed SQL/HBA files, correct permissions, persistent
replication slots, fast checkpoints and native quorum readiness checks. See the
cluster README for the failed attempt, diagnosis and remaining production-test
gaps. Copies share one physical host; pageserver/S3/controller failures and
independent-backup restore remain unqualified.
