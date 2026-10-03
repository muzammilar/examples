# Skytable — single node

One Skytable server (`skyd`) on Docker Compose with a password-protected `root` account, BlueQL
through the bundled shell `skysh`, and Skytable's load generator `sky-bench`.

## Quick start

```bash
make up        # start skyd (data in a named volume)
make test      # blueql/1-root.blueql as root, blueql/2-app-user.blueql as a standard user,
               # then a restart that reads a row back from disk
make benchmark # sky-bench uniform_std_v1: 1M rows inserted, updated, selected, deleted (SMOKE=1: 100k)
make status    # container state, server version, INSPECT GLOBAL
make cli       # interactive skysh as root
make down      # remove the container, the data volume and the built bench image
```

## Setup

| item | value |
|------|-------|
| client port | `localhost:2003`, Skyhash over TCP (`SKYTABLE_PORT`) |
| image | `skytable/skytable:146d866452d937da8a987c92b3a12d42588ee86f` (`SKYTABLE_IMAGE_TAG`) |
| version | skyd and skysh **v0.8.4** (latest release) |
| data | `/var/lib/skytable`: `gns.db-tlog` (spaces/models/users), `data/` (rows) |
| healthcheck | `skysh -e 'sysctl report status'` |

- **Image.** Built by Skytable's CI from the `next` branch on 2026-09-30; multi-arch, native on
  arm64. The release tag `skytable/skytable:v0.8.4` is amd64 only (emulated on Apple Silicon).
  Since v0.8.4 the newer build has only CI changes, compile fixes and skysh fixes (the unreleased
  0.8.5 changelog).
- **Config.** The image's entrypoint writes a random root password into its config file and
  prints it once. Compose instead runs `skyd` directly with env vars: `SKYDB_RUN_MODE=prod`,
  `SKYDB_ENDPOINTS=tcp@0.0.0.0:2003`, `SKYDB_AUTH_PLUGIN=pwd`, `SKYDB_AUTH_ROOT_PASSWORD`
  (`SKYTABLE_PASSWORD`, default `skytable-root-password`; skyd refuses fewer than 16 characters).
  `SKYDB_PASSWORD` in the container lets `skysh` log in as root without `--password`.
- **Users.** One `root` account (DDL and user management). Standard users from
  `sysctl create user app with { password: "..." }` can only run DML, `INSPECT` and
  `sysctl report status`.
- **Durability.** DDL and DCL are durable when they return. DML is *eventually* durable: changed
  rows reach disk within the reliability service window (`SKYTABLE_SERVICE_WINDOW`, default
  300 s) and on a clean shutdown (SIGTERM). `make test` restarts the container and reads `alice`
  back.

## BlueQL

Looks like SQL, works differently. [`blueql/`](blueql) shows each point:

- A **space** holds **models**. The first field is the primary key unless another is marked
  `primary`. Fields are not nullable unless declared `null`. Types: `bool`, `uint8..64`,
  `sint8..64`, `float32/64`, `string`, `binary`, lists (`[string]`).
- `SELECT`, `UPDATE`, `DELETE` are **point queries on the primary key** (`WHERE pk = ?`). No
  secondary indexes, range queries or joins. Several rows need `SELECT ALL ... LIMIT n`, a scan.
- `UPDATE` supports `=`, `+=`, `-=`, `*=`, `/=` on numbers, and `+=` to append to a string or list.
- Literals are always sent as parameters (`?`); drivers bind them, skysh turns typed literals into
  parameters. No comments, no semicolons, one statement per query.

| error code | meaning |
|-----------:|---------|
| `108` | duplicate primary key |
| `109` | data validation |
| `111` | row not found |
| `5` | permission denied |

## Benchmark

`make benchmark` runs `sky-bench` from the official v0.8.4 release bundle
([`bench/Dockerfile`](bench/Dockerfile), native arm64/x86_64; the server image doesn't ship it) in
a container sharing the server's network namespace ([`bench/run.sh`](bench/run.sh)).

Workload `uniform_std_v1`: creates model `db.db(k: binary, v: uint64)`, then four phases over
`ROWS` unique keys (default 1,000,000): `INSERT`, `UPDATE v += 1`, `SELECT v`, `DELETE`, one query
per key per phase, from `CONNECTIONS` connections (32) on `THREADS` client threads (4). Every
query is its own round trip (no pipelining); latency is per query. The model is dropped at the end.

```bash
make benchmark                    # 1M rows = 4M queries
make benchmark SMOKE=1            # 100k rows
make benchmark CONNECTIONS=128 ROWS=2000000
```

- Output: summary table (queries/s = sky-bench's "full" throughput, latency
  mean/p50/p95/p99/max); raw output, log and JSON (version, parameters, Docker VM CPUs/memory,
  applied `limits`) in `results/skytable-single-<UTC time>.{txt,log,json}` (gitignored), written
  by [`bench/report.py`](bench/report.py) (standard library, `uv run --frozen` in
  `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`).
- Resource caps: [`bench/limits.sh`](bench/limits.sh) caps the `skytable` container at
  `BENCH_CPUS=4` / `BENCH_MEM=4g` (no swap) with `docker update` and restores the old limits
  afterwards. Docker cannot remove a memory limit from a running container, so "unlimited" goes
  back as the Docker VM's total memory; `make down && make up` starts clean. The bench client has
  `cpus: 4` (`BENCH_CLIENT_CPUS`). skyd starts at least one worker thread per logical CPU it sees
  (11 in this VM); the CPU cap only limits their CPU time.

### Sample results

2026-10-02, defaults unless noted, Docker Desktop 29.5.3, Apple M4 Pro (Docker VM: 11 CPUs,
24.4 GB, aarch64, native image), Skytable 0.8.4, server 4 CPUs / 4 GB, client 4 CPUs, 1,000,000
rows per phase.

| connections | INSERT /s | UPDATE /s | SELECT /s | DELETE /s | SELECT p50 ms | SELECT p99 ms |
|------------:|----------:|----------:|----------:|----------:|--------------:|--------------:|
| 8 | 146,903 | 128,316 | 122,064 | 89,286 | 0.051 | 0.289 |
| 32 (default) | 202,363 / 185,992 | 211,374 / 211,223 | 175,719 / 205,504 | 165,351 / 170,008 | 0.137 / 0.131 | 1.088 / 0.620 |
| 128 | 177,286 | 177,041 | 254,876 | 213,273 | 0.404 | 2.571 |
| 32, server 8 CPUs | 165,364 | 198,491 | 221,738 | 194,392 | 0.122 | 0.588 |

- ~200k point queries/s at p50 ~0.13 ms (0.05 ms at 8 connections), one round trip per query.
- Doubling server CPUs changes nothing: skyd uses ~1.8 CPUs and the client ~1.5. The cost is
  the round trip per query; more connections mostly trade latency for a little throughput.
- `sky-bench` 0.8.4 does not pipeline. [`../session-store`](../session-store) measures
  pipelining with the Rust driver.
- Each phase takes ~5 s; repeat a run before comparing small differences.

## Known issues

skysh/skyd 0.8.4:

- skysh cannot send a signed integer: `-5` fails in its literal parser; an unsigned literal into a
  `sint64` column is error `109`, and so is `-=`. Use a driver for `sint*` columns.
- skysh reads a float up to the next punctuation, so `SET rating = 4.9 WHERE ...` fails to parse.
  Put a float before a comma or `)` (`SET rating = 4.9, followers += 1 WHERE ...`).
- After `ALTER MODEL ... ADD` on a model with rows, `SELECT *` on an old row returns `101` and
  `SELECT ALL` panics the server task (`sel.rs:108`). The connection is reset; skyd keeps running.
- `sysctl create user` takes an unquoted name (`app`, not `"app"`: error `29`).
