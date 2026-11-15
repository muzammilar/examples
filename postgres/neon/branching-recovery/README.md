# Neon: branching and storage/compute recovery

Three durable WAL safekeepers, one pageserver, a storage broker and compute, backed by Garage S3; compare the same orders with PostgreSQL primary + two synchronous candidates.

The Go application uses pgx COPY and deterministic payloads. Python controllers use
native Neon APIs and psycopg server cursors for independent full-data verification.
The pinned self-hosted snapshot uses PostgreSQL **17.5**; the plain baseline is
**17.6**. This is a version-specific learning lab, not a current patched deployment
recommendation. Its emergency control-plane mode and single pageserver are explicit
availability limits; there is no production storage controller or automatic pageserver failover.

## Quick start

Prerequisites: Docker Compose, Make and Python 3. Native ARM64 images were tested.
Allow 16 GiB Docker RAM / 8 GiB free disk.

1. `make docker-up` — build the Go workload/Python controller image and start storage services.
2. `make up` — create the tenant/timeline, start compute and COPY one million orders per backend.
3. `make test` — read every row/body; verify native durability settings and both PostgreSQL standbys.
4. `make down` — remove workload tables and the baseline branch database.
5. `make docker-down` — remove only this project’s containers, volumes, network and built client image.

`ROWS=1000000` is a Make variable. Ten thousand customers own deterministic orders;
each payload is a distinct SHA-256 of its order ID. COPY batches contain 10,000 rows.
After destructive teardown, archive the old `results/` before a fresh run; the
controller refuses to substitute a new tenant for a missing recorded tenant or
overwrite an existing seed/journal. This preserves failed experiments.

## Setup and durability

| Role | Configuration / limits |
|---|---|
| Compute | Native `compute_ctl`, PostgreSQL 17.5, 2 CPUs / 2 GiB, 128 MiB shared buffers; ephemeral `/tmp` |
| Pageserver | Pinned Neon storage binary, 2 CPUs / 2 GiB; 1 instance |
| Safekeepers | 3 members; majority WAL acknowledgements; each 1 CPU / 512 MiB |
| Broker | Native storage broker, 0.5 CPU / 256 MiB |
| Plain PostgreSQL | 17.6; primary 2 CPUs / 2 GiB; two replicas each 1 CPU / 1 GiB, `ANY 1 (pg1,pg2)`, persistent replication slots |
| Workload/controller | Go 1.25.4 / pgx 5.7.6; Python / psycopg 3.2.12; 2 CPUs / 2 GiB |
| S3 | Garage 2.5.0: nine peers / three logical zones / three copies; separate from compute/WAL disks |
| Optional branch compute | Extra 2 CPUs / 2 GiB while the native branch endpoint runs |

Both servers must report `fsync=on` and `synchronous_commit=on`. Neon uses native
`synchronous_standby_names=walproposer`; PostgreSQL requires one of two streaming quorum candidates.
Commit acknowledgement, pageserver ingestion and S3/local archive upload are
different stages. Checkpoint/archive completion is not independently timed here.
All copies share the same physical Docker host; there is no host-power-loss proof.
Whole-stack resource/dependency costs differ, so this is not an equal-cost ranking.

## Validation status

The measurements below are from the earlier passing deployment. The latest
source-materialization and live-reader changes have not completed a fresh
end-to-end lifecycle for this shape. Compilation/static checks do not replace
that pending runtime validation.

## Historical measured results

2026-11-15, Apple M4 Pro, shared Docker VM aarch64 (11 CPUs / 24.42 GiB).
One run per baseline/query; limits are above. Each backend retained and verified
**1,000,000 rows and 32,000,000 SHA-256 payload bytes**, with **0 missing, duplicate
or incorrect rows**. Both PostgreSQL replicas were also fully read after replay reached the primary LSN.

COPY batch latency includes row generation, encoding, transfer and commit response;
it excludes the subsequent witness fsync. Total elapsed/throughput includes the
fsynced witness. This is a short backfill with warm caches, not steady-state capacity.

| Backend | COPY elapsed (s) | Rows/s | 10,000-row batch p50 / p99 (ms) |
|---|---:|---:|---:|
| neon | 3.664 | 272,889 | 27.285 / 135.195 |
| postgres | 2.420 | 413,229 | 17.124 / 103.332 |

Plain PostgreSQL's observed COPY rate was 1.51× Neon's in this run; the stacks have different resource budgets and durability paths.

One warmup + 20 measured queries; every count/sum is checked independently.
The reported p99 is the largest of only 20 observations.

| Query | Neon p50 / p99 (ms) | PostgreSQL p50 / p99 (ms) | Neon/PostgreSQL p99 ratio |
|---|---:|---:|---:|
| customer-orders | 0.089 / 0.253 | 0.124 / 0.264 | 0.96× |
| all-orders | 33.881 / 56.293 | 15.348 / 44.101 | 1.28× |

## What Neon demonstrates: native copy-on-write branching

`make branch` creates a timeline at the parent’s flushed LSN and starts a separate
compute. All million parent rows are verified in the branch; a branch-only update
must leave the parent unchanged. A plain PostgreSQL `pg_dump` + `pg_restore` into
another database provides a full logical-copy baseline; it is not copy-on-write.

| Operation | Measured |
|---|---:|
| Neon timeline-create API only | 5.853 ms |
| Native branch through first verified SQL row, including compute startup | 6.445 s |
| PostgreSQL full logical dump + restore, database creation excluded | 5.197 s |
| Logical dump bytes | 46,663,913 bytes |

Metadata creation was cheap, but startup dominated usable-branch time; this run
does **not** show a faster ready-to-query branch than the full-copy baseline.
The native ancestor timeline/LSN and isolation checks demonstrate the branching
mechanism. Shared-layer storage savings and large-dataset branch scaling were not measured.

## Independent failure and recovery tests

Run `make test-production` on a fresh seeded fixture, or run individual cases.
The aggregate includes the branch experiment once; it deliberately refuses an
already-created branch. It runs tests serially and propagates failures.

| Command | Fault / native recovery |
|---|---|
| `make failover` | SIGKILL sk0, retain its disk, keep writes running for a 30-second outage, restore and wait for all three WAL copies |
| `make disk-failure` | Fence sk0, delete only its label-verified owned volume, start empty, enable native peer recovery and invoke native `pull_timeline`; require all three flush/commit LSNs to reach witnessed WAL |
| `make failover FAULT_SERVICE=sk1` | Fail a different member after disk repair; continued acknowledgements demonstrate the rebuilt sk0 participates in quorum |
| `make chaos-compute` | SIGKILL compute with its volume retained; reconnect and reconcile complete state after restart |
| `make compute-replacement` | Delete the owned compute volume; bootstrap fresh from pageserver/WAL and verify all baseline and recovery data |

The Go producer attempts 100-row COPY transactions with a 100 ms pause, bounded
connection/statement timeouts and unique IDs. It does not blindly replay ambiguous
mutations. An external fsynced journal records attempts, ACKs, errors and flush LSNs.
Post-recovery progress requires three new acknowledged batches before stopping.
Independent verification checks every payload and historical ACK and rejects partial
COPY transactions, including partially visible ambiguous transactions. This bounded
fault load is not a throughput benchmark.

| Latest case | Recovery incl. 30 s hold (s) | ACK rows | Ambiguous transactions |
|---|---:|---:|---:|
| process / sk0 | 37.624 | 34,800 | 0 |
| disk / sk0 | 39.447 | 37,900 | 0 |
| process / sk1 | 37.579 | 35,200 | 0 |
| compute / compute | 37.972 | 6,000 | 280 |
| cache / compute | 38.595 | 5,800 | 303 |

Full historical verification checked **120,500 acknowledged rows across 6 journals**,
with **0 missing ACKs, 0 wrong/duplicate rows and 0 partial transactions**.
The history includes the failed compute attempt; it is not a passing recovery run.
The empty-WAL case’s `pull_timeline` returned `safekeeper_host=null`: the active
proposer/native peer path had already created the timeline. Its final WAL LSNs
and the later sk1-outage writes establish recovery, not the API return alone.

## Gotchas and limits

- This pinned self-hosted image contains PostgreSQL 17.5. It uses the upstream
  emergency control-plane path and one pageserver; managed Neon’s HA, autoscaling,
  production security and upgrades are not qualified by these results.
- Runtime `/tmp` is tmpfs. A fresh clustered SIGKILL/restart run failed on a stale
  Unix-socket lock in Docker’s persistent writable layer. It was recovered with
  the database volumes retained, then compute-crash and empty-cache tests passed.
  The failed journal/diagnostics remain in the clustered results.
- PostgreSQL initialization uses SQL/HBA files, avoiding execution of a bind-mounted
  shell script. Standbys need mode 0700. Persistent slots prevent required WAL from
  disappearing; fast basebackup checkpoints avoid the default spread-checkpoint
  wait. Fast checkpoints trade extra I/O burst for shorter bootstrap.
- An empty command-line `synchronous_standby_names` overrides `ALTER SYSTEM` and
  would make the comparison asynchronous. It is omitted; native quorum state is
  checked before seeding. A Ready PostgreSQL container alone is insufficient.
- The native compute JSON requires `vartype` on every setting; the original
  appended `synchronous_commit` entry failed schema parsing until corrected.
- Not tested: pageserver loss/rebuild, S3 outage, quorum loss, storage-controller
  failover, network partition, TLS/authentication, upgrades, resource exhaustion,
  sustained load or recovery from an independent backup. One failed multi-component host is outside this lab’s redundancy boundary.

The full five-command cleanup passed. Results and failed attempts remain under
ignored `results/`; bootstrap-only evidence was archived separately before the
fresh measured clustered fixture. Local credentials/plaintext endpoints are lab
configuration, not a security qualification.

## Sources

- [Pinned upstream self-hosted topology](https://github.com/neondatabase/neon/blob/77e22e4bf09d88b70b4a83a38c2f6de6301816b4/docs/docker.md).
- [Pinned pageserver timeline/branch APIs](https://github.com/neondatabase/neon/blob/77e22e4bf09d88b70b4a83a38c2f6de6301816b4/pageserver/src/http/routes.rs).
- [Pinned safekeeper recovery/status APIs](https://github.com/neondatabase/neon/blob/77e22e4bf09d88b70b4a83a38c2f6de6301816b4/safekeeper/src/http/routes.rs).
- [Pinned local/S3 storage configuration](https://github.com/neondatabase/neon/blob/77e22e4bf09d88b70b4a83a38c2f6de6301816b4/libs/remote_storage/src/config.rs).
- [PostgreSQL basebackup/slot/checkpoint options](https://www.postgresql.org/docs/17/app-pgbasebackup.html).
- [pgx native COPY implementation](https://github.com/jackc/pgx/blob/v5.7.6/copy_from.go).

## Source and build

`orders-workload/` is a tracked symlink to this system’s workload source.
The first lifecycle command runs `prepare.py` to materialize regular files in
`.build/orders-workload/`, which is the local Docker build context. `.build/` is
gitignored and is never committed. The application runs as this example’s
Compose service or Kubernetes workload Pod; its lifecycle needs no sibling
example’s running services or generated state.
