# Neon: minimal single-copy deployment

One copy of each Neon component, using the native local-filesystem storage backend, versus one local PostgreSQL server. This is a non-HA deployment: neither its local archive nor its WAL is an independent backup.

The Go application uses pgx COPY and deterministic payloads. Python controllers use
native Neon APIs and psycopg server cursors for independent full-data verification.
The pinned self-hosted snapshot uses PostgreSQL **17.5**; the plain baseline is
**17.6**. This is a version-specific learning lab, not a current patched deployment
recommendation. Its emergency control-plane mode and single pageserver are explicit
availability limits; there is no production storage controller or automatic pageserver failover.

## Quick start

Prerequisites: Docker Compose, Make and Python 3. Native ARM64 images were tested.
Allow 8 GiB Docker RAM / 4 GiB free disk.

1. `make docker-up` — build the Go workload/Python controller image and start storage services.
2. `make up` — create the tenant/timeline, start compute and COPY one million orders per backend.
3. `make test` — read every row/body; verify native durability settings.
4. `make down` — remove workload tables.
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
| Safekeepers | 1 member; local WAL; 1 CPU / 512 MiB |
| Broker | Native storage broker, 0.5 CPU / 256 MiB |
| Plain PostgreSQL | 17.6; primary 2 CPUs / 2 GiB; no replicas |
| Workload/controller | Go 1.25.4 / pgx 5.7.6; Python / psycopg 3.2.12; 2 CPUs / 2 GiB |

Both servers must report `fsync=on` and `synchronous_commit=on`. Neon uses native
`synchronous_standby_names=walproposer`.
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
or incorrect rows**. No failover capability is claimed for this shape.

COPY batch latency includes row generation, encoding, transfer and commit response;
it excludes the subsequent witness fsync. Total elapsed/throughput includes the
fsynced witness. This is a short backfill with warm caches, not steady-state capacity.

| Backend | COPY elapsed (s) | Rows/s | 10,000-row batch p50 / p99 (ms) |
|---|---:|---:|---:|
| neon | 2.807 | 356,233 | 21.647 / 107.300 |
| postgres | 1.812 | 551,829 | 13.990 / 154.096 |

Plain PostgreSQL's observed COPY rate was 1.55× Neon's in this run; the stacks have different resource budgets and durability paths.

One warmup + 20 measured queries; every count/sum is checked independently.
The reported p99 is the largest of only 20 observations.

| Query | Neon p50 / p99 (ms) | PostgreSQL p50 / p99 (ms) | Neon/PostgreSQL p99 ratio |
|---|---:|---:|---:|
| customer-orders | 0.117 / 0.347 | 0.095 / 0.265 | 1.31× |
| all-orders | 30.683 / 52.627 | 15.662 / 45.706 | 1.15× |

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
  sustained load or recovery from an independent backup. The minimal shape has no redundant WAL/storage copy.

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
