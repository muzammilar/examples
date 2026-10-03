# Skytable — single node

One Skytable server (`skyd`) on Docker Compose, with a password-protected `root` account, BlueQL
through the bundled shell `skysh`, and Skytable's own load generator `sky-bench`.
Client port: `localhost:2003` (Skyhash protocol over TCP; override with `SKYTABLE_PORT`).

```bash
make up        # start skyd (data in a named volume)
make test      # blueql/1-root.blueql as root, blueql/2-app-user.blueql as a standard user,
               # then a restart that reads a row back from disk
make benchmark # sky-bench uniform_std_v1: 1M rows inserted, updated, selected, deleted (SMOKE=1: 100k)
make status    # container state, server version, INSPECT GLOBAL
make cli       # interactive skysh as root
make down      # remove the container, the data volume and the built bench image
```

- Image `skytable/skytable:146d866452d937da8a987c92b3a12d42588ee86f` (override the tag with
  `SKYTABLE_IMAGE_TAG`): skyd and skysh **v0.8.4**, the latest release, built by Skytable's CI from
  the `next` branch on 2026-09-30. It is multi-arch and runs natively on arm64. The release tag
  `skytable/skytable:v0.8.4` is amd64 only, and on an Apple Silicon Mac it would run under
  emulation. The newer build has only CI changes, compile fixes and skysh fixes (the unreleased
  0.8.5 changelog) since v0.8.4.
- The image's entrypoint writes a random root password into its config file and prints it once.
  Compose runs `skyd` directly and configures it through environment variables instead:
  `SKYDB_RUN_MODE=prod`, `SKYDB_ENDPOINTS=tcp@0.0.0.0:2003`, `SKYDB_AUTH_PLUGIN=pwd` and
  `SKYDB_AUTH_ROOT_PASSWORD` (`SKYTABLE_PASSWORD`, default `skytable-root-password`; skyd
  refuses fewer than 16 characters). `SKYDB_PASSWORD` in the container lets `skysh` log in as root
  without `--password`.
- There is one `root` account (DDL and user management). Standard users from
  `sysctl create user app with { password: "..." }` can only run DML, `INSPECT` and
  `sysctl report status`.
- Data lives in `/var/lib/skytable` (`gns.db-tlog` for spaces/models/users, `data/` for rows).
  DDL and DCL are durable when they return. DML is *eventually* durable: changed rows reach disk
  within the reliability service window (`SKYTABLE_SERVICE_WINDOW`, default 300 s), and on a
  clean shutdown (SIGTERM). `make test` restarts the container and reads `alice` back.
- Healthcheck: `skysh -e 'sysctl report status'`.

## BlueQL

BlueQL looks like SQL, but works differently, and [`blueql/`](blueql) shows each of these:

- A **space** holds **models**. The first field is the primary key unless another is marked
  `primary`. Fields are not nullable unless declared `null`. Types: `bool`, `uint8..64`,
  `sint8..64`, `float32/64`, `string`, `binary`, lists (`[string]`).
- `SELECT`, `UPDATE` and `DELETE` are **point queries on the primary key** (`WHERE pk = ?`). There
  are no secondary indexes, no range queries, no joins. Several rows need `SELECT ALL ... LIMIT n`,
  a scan.
- `UPDATE` supports `=`, `+=`, `-=`, `*=`, `/=` on numbers, and `+=` to append to a string or list.
- Literals are always sent as parameters (`?`). Drivers bind them, and skysh turns typed literals
  into parameters for you. There are no comments, no semicolons and one statement per query.
- Errors come back as codes. The ones the demo hits: `108` duplicate primary key, `109` data
  validation, `111` row not found, `5` permission denied.

Quirks in skysh/skyd 0.8.4 found while writing the demo:

- skysh cannot send a signed integer. `-5` fails in its literal parser, an unsigned literal into a
  `sint64` column is error `109`, and so is `-=`. Use a driver for `sint*` columns.
- skysh reads a float up to the next punctuation, so `SET rating = 4.9 WHERE ...` fails to parse.
  Put a float before a comma or `)` (`SET rating = 4.9, followers += 1 WHERE ...`).
- After `ALTER MODEL ... ADD` on a model that already has rows, `SELECT *` on an old row returns
  `101`, and `SELECT ALL` panics the server task (`sel.rs:108`). The connection is reset, and skyd
  keeps running.
- `sysctl create user` takes an unquoted name (`app`, not `"app"`: error `29`).

## Benchmark

`make benchmark` runs `sky-bench`, Skytable's own load generator, from the official v0.8.4 release
bundle ([`bench/Dockerfile`](bench/Dockerfile), native arm64/x86_64; the server image doesn't
ship it). It runs in a container sharing the server's network namespace
([`bench/run.sh`](bench/run.sh)). Workload `uniform_std_v1`: it creates model
`db.db(k: binary, v: uint64)`, then runs four phases over `ROWS` unique keys (default 1,000,000):
`INSERT`, `UPDATE v += 1`, `SELECT v`, `DELETE`. Each key gets one query per phase, sent from
`CONNECTIONS` connections (32) on `THREADS` client threads (4). Every query is its own round trip
(no pipelining), and latency is per query. The model is dropped at the end.

```bash
make benchmark                    # 1M rows = 4M queries
make benchmark SMOKE=1            # 100k rows
make benchmark CONNECTIONS=128 ROWS=2000000
```

It prints a summary table (queries/s = sky-bench's "full" throughput, latency mean/p50/p95/p99/max)
and keeps the raw output, its log and parsed JSON with the version, parameters and Docker VM
CPUs/memory in `results/skytable-single-<UTC time>.{txt,log,json}` (gitignored), written by
[`bench/report.py`](bench/report.py) (standard library, `uv run --frozen` in
`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`).

**Resource budget.** [`bench/limits.sh`](bench/limits.sh) caps the `skytable` container at
`BENCH_CPUS=4` / `BENCH_MEM=4g` (no swap) with `docker update` for the run and restores the old
limits afterwards. Docker cannot remove a memory limit from a running container, so "unlimited"
goes back as the Docker VM's total memory; `make down && make up` starts clean. The bench client
has `cpus: 4` (`BENCH_CLIENT_CPUS`). The JSON records the applied limits under `limits`. skyd
starts at least one worker thread per logical CPU it sees (11 in this VM), and the CPU cap only
limits how much CPU time they get.

### Sample results

2026-10-02, `make benchmark` (defaults unless noted), Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64, native image), Skytable 0.8.4, the server capped at
4 CPUs / 4 GB, client 4 CPUs, 1,000,000 rows per phase.

| connections | INSERT /s | UPDATE /s | SELECT /s | DELETE /s | SELECT p50 ms | SELECT p99 ms |
|------------:|----------:|----------:|----------:|----------:|--------------:|--------------:|
| 8 | 146,903 | 128,316 | 122,064 | 89,286 | 0.051 | 0.289 |
| 32 (default) | 202,363 / 185,992 | 211,374 / 211,223 | 175,719 / 205,504 | 165,351 / 170,008 | 0.137 / 0.131 | 1.088 / 0.620 |
| 128 | 177,286 | 177,041 | 254,876 | 213,273 | 0.404 | 2.571 |
| 32, server 8 CPUs | 165,364 | 198,491 | 221,738 | 194,392 | 0.122 | 0.588 |

- About 200k point queries/s at a p50 around 0.13 ms (and 0.05 ms at 8 connections), every
  query its own round trip.
- Doubling the server's CPUs changes nothing. During a run skyd uses ~1.8 CPUs and the client
  ~1.5, so neither is the limit. The cost is one network round trip per query, and more
  connections mostly trade latency for a little throughput.
- Pipelining (many queries per round trip, from Skytable drivers) removes that cost. `sky-bench`
  0.8.4 does not pipeline.
- Each phase takes about 5 s, so repeat a run before comparing small differences.
