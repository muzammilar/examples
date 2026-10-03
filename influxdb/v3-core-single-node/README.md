# InfluxDB 3 Core — single node

One InfluxDB 3 Core server (`influxdb3 serve`) with MinIO as its object store. The server keeps
no local state: the WAL, the catalog, snapshots and the Parquet files all go to the S3 bucket
`influxdb3`. A one-shot `init` service writes an offline admin token
(`influxdb3 create token --admin --offline`), and the server loads it with `--admin-token-file`.
HTTP API: `localhost:8191` (override with `INFLUXDB_PORT`).

```bash
make up         # MinIO, the bucket, the admin token, then influxdb3 serve (healthy in ~2-8 s)
make test       # scripts/demo.sh (below), then the bucket's objects as MinIO lists them
make benchmark  # ingest + query latency, bench/bench.py (SMOKE=1: 200k rows)
make status     # containers, /ping, databases
make cli        # bash in the server container with INFLUXDB3_AUTH_TOKEN set
make files      # just the bucket listing
make down       # remove the containers and volumes (data, token)
```

What `make test` does ([`scripts/demo.sh`](scripts/demo.sh), the `influxdb3` CLI and curl from
the same image; it drops and recreates database `home`, so it can be repeated):

1. **auth**: a request without a token gets 401. Creates a named admin token with a 1 h expiry,
   lists tokens (`influxdb3 show tokens`) and deletes it. Core has admin tokens only;
   database-scoped (resource) tokens are an Enterprise feature.
2. **database** `home` with `--retention-period 30d`.
3. **writes**: 90 lines of line protocol (`home,room=Kitchen temp=21.0,hum=35.9,co=0i <ts>`,
   3 rooms, one a minute for 30 min) to `/api/v3/write_lp`, plus one line each through the
   v1 `/write` and v2 `/api/v2/write` endpoints. The schema comes from the writes, with no DDL.
   A request with a mistyped field (`temp="warm"`) gets `400 partial write of line protocol
   occurred`, and its good lines are kept.
4. **SQL** (`/api/v3/query_sql` and `influxdb3 query`, which uses Flight): per-room aggregates,
   `date_bin` 10-minute buckets, `information_schema.columns` (tags are
   `Dictionary(Int32, Utf8)`).
5. **InfluxQL**: `/api/v3/query_influxql` (`MEAN`, `MAX ... GROUP BY room`) and the v1 `/query`
   endpoint (`SHOW TAG VALUES`).
6. **caches**: a last value cache (`--key-columns room --value-columns temp,hum,co`) and a distinct
   value cache on `room`, queried with `last_cache('home', 'home_last')` and
   `distinct_cache('home', 'home_rooms')` (SQL only). The caches fill only from writes that
   arrive after they are created, so the demo writes one more reading per room.
7. **processing engine** (Python embedded in the server; `--plugin-dir` is
   [`plugins/`](plugins)): a WAL trigger (`table:home`, [`temp_alert.py`](plugins/temp_alert.py))
   writes a `home_alerts` row for every reading above `max_temp=23`. A schedule trigger
   (`every:5s`, [`rollup.py`](plugins/rollup.py)) runs a SQL aggregate over the last 15 min and
   writes it to `home_rollup`. Plugin log lines show up in `system.processing_engine_logs`. The
   `rollup` trigger keeps running every 5 s after the demo; stop it with
   `influxdb3 disable trigger --database home rollup` (in `make cli`).
8. **Parquet**: waits for `system.parquet_files` to list the `home` files, one per 10-minute
   chunk (`--gen1-duration`), e.g. `node0/dbs/1/0/2026-10-03/06-10/0000000030.parquet`
   (`dbs/<db id>/<table id>/<date>/<chunk>/<snapshot seq>`). `make files` then lists the
   same objects in MinIO next to `node0/wal/*.wal` (one per second with writes), the catalog and
   `snapshots/*.info.json`.

Notes:

- Image `influxdb:3.12.0-core` (override with `INFLUXDB_VERSION`). MinIO no longer publishes
  community images, so this uses Chainguard's free build pinned by digest, as
  [`../../milvus/single-node`](../../milvus/single-node) does. MinIO is not published to the host
  (`minioadmin`/`minioadmin`).
- `--wal-files-per-snapshot=10` (default 600) snapshots every ~10 WAL files (~10 s of writes)
  instead of every ~10 minutes, so Parquet files appear while you watch. Each snapshot writes one
  file per table per 10-minute chunk it touches. Core has no compactor, so small files stay small.
  Override with `WAL_FILES_PER_SNAPSHOT` / `GEN1_DURATION`.
- `--disable-authz=health,ping` leaves `/health` and `/ping` open for the healthcheck. Every
  other endpoint needs `Authorization: Bearer <token>`.
- `--virtual-env-location` keeps the plugin venv the server creates out of the bind-mounted
  `plugins/` (mounted read-only).
- Without `--admin-token-file` you would run `influxdb3 create token --admin` once against the
  running server and save the token it prints. The token cannot be shown again
  (`--regenerate` replaces it).

## Benchmark

`make benchmark` runs [`bench/bench.py`](bench/bench.py) (standard library, `uv run --frozen` in
`ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`) against the running server. It recreates
database `bench` and creates a last value cache (key `host`) and a distinct value cache
(`region, host`) on `cpu`. Then it posts `ROWS` rows (default 2,000,000) for `SERIES` hosts
(10,000) to `/api/v3/write_lp`: `cpu,host=host-N,region=rM usage_user=..,usage_system=..,usage_idle=..,load=..i`,
one point per host every 10 s ending now, in time order. The requests are pre-built, `BATCH`
lines each (10,000, ~1.1 MB), and `WRITERS` threads (16) post them with keep-alive connections.
`NO_SYNC=1` adds `no_sync=true`. Then it waits until `count(*)` sees every row and runs each
query `ITER` (30) times through `/api/v3/query_sql` (JSON). The result goes to
`results/influxdb3-single-<UTC time>.json` (gitignored).

**Resource budget.** [`bench/limits.sh`](bench/limits.sh) caps the containers for the run with
`docker update`: `BENCH_CPUS=4` / `BENCH_MEM=6g` in total. MinIO gets a fixed 1 CPU / 1 GB, which
leaves `influxdb3` 3 CPUs / 5 GB. The old limits are restored afterwards. The bench client has
2 CPUs (`BENCH_CLIENT_CPUS`).

### Sample results

2026-10-02, Docker Desktop on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GiB, aarch64, native
images), InfluxDB 3 Core 3.12.0, the limits above, 2M rows / 10k series, 10k lines per request:

| writers | ack | rows/s | MB/s of LP | request p50 | request p99 |
|--------:|-----|-------:|-----------:|------------:|------------:|
| 4 | after WAL flush (default) | 40,342 | 4.5 | 996 ms | 1,320 ms |
| 16 (default) | after WAL flush | 161,920 | 17.9 | 971 ms | 1,362 ms |
| 64 | after WAL flush | 200,806 | 22.2 | 2,520 ms | 4,287 ms |
| 16 | `no_sync=true` | 1,165,181 | 128.8 | 114 ms | 284 ms |

A durable write returns only after the next WAL flush to the object store
(`--wal-flush-interval`, 1 s). Every request waits about a second, so throughput comes from
concurrency: 4 → 16 writers is 4x the rows/s at the same latency. At 64 writers the server's
3 CPUs are the limit. With `no_sync=true` the server acks once the request is parsed and
validated, 7x faster, but a crash loses the unflushed rows, and the rows only became queryable
1.7 s after the last ack. In earlier runs, queries issued right after the acks saw part of the
data.

Queries, default run (16 writers), 30 runs each, p50 / p99 ms:

| query | rows | p50 | p99 |
|-------|-----:|----:|----:|
| `count(*)` over 2M rows | 1 | 226 | 574 |
| `avg(usage_user)` by region, last 5 min | 16 | 95 | 172 |
| 1 host, all 200 points | 200 | 12.3 | 28 |
| 1 host latest: `ORDER BY time DESC LIMIT 1` | 1 | 7.4 | 22 |
| 1 host latest: `last_cache()` | 1 | **0.97** | 4.9 |
| all 10k hosts latest: `last_value(... ORDER BY time) GROUP BY host` | 10,000 | 156 | 262 |
| all 10k hosts latest: `last_cache()` | 10,000 | 177 | 443 |
| distinct (region, host): `SELECT DISTINCT` | 10,000 | 202 | 434 |
| distinct (region, host): `distinct_cache()` | 10,000 | **6.1** | 25 |

- Keyed lookups are where the caches pay off. One host's latest values come back in under
  1 ms from the last value cache, against 2–12 ms in SQL across runs. The 10k-row distinct list
  takes 3–6 ms from the distinct value cache, against 13–200 ms with `SELECT DISTINCT`.
- Dumping the whole last value cache (10k keys, no key predicate) was never faster than the
  SQL `GROUP BY` (177–426 ms against 17–194 ms across four runs). Query it by key.
- Other agents shared the Docker VM during these runs, so the scan-type queries varied 2–10x
  between runs (`count(*)` p50 from 0.8 to 226 ms). Compare within a run and repeat before
  trusting small differences.
