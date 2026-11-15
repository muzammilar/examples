# Neon: storage/compute separation

Website: [neon.com](https://neon.com/)  
GitHub: [neondatabase/neon](https://github.com/neondatabase/neon)

The [Kubernetes storage/recovery example](kubernetes-statefulset/) runs native
compute, pageserver, broker and three safekeepers in its own kind cluster/context,
with owned Garage S3 and a three-copy PostgreSQL comparison. It demonstrates
storage/compute separation, ancestor-LSN branching, compute-cache replacement and
WAL/worker recovery with independent live writers/readers and an external witness.

On 2026-11-15, full parent/branch and PostgreSQL-copy checks passed at one million
orders per backend. Five recovery cases passed; complete history reconciled
**300,400 acknowledged recovery rows across eight journals**, with zero missing,
wrong/duplicate values or partial COPY transactions. Failed attempts and bootstrap
gotchas remain documented. The full lifecycle and owned cleanup passed.

Observed COPY rates were **247,849 rows/s Neon / 411,839 PostgreSQL**. Branch API
creation took **4.917 ms**, usable branch SQL including deployment **6.291 s**,
and a full PostgreSQL logical copy **3.503 s**; these have different semantics.
The example README records resource budgets, warm-query timings, phase reports and
limits. Results do not establish equal-budget capacity or independent-host durability.

The pinned self-hosted PostgreSQL 17.5 snapshot uses emergency control-plane mode
and one pageserver. This is a StatefulSet lab, not an official Neon operator or
managed Neon HA/security/upgrade qualification. Single-node and Compose examples
are being validated separately.

`orders-workload/` contains the tracked Go/pgx application and native Python
controllers. Each example exposes a local source symlink and materializes an
ignored `.build/` context before building; generated files are never committed.
