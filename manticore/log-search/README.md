# Manticore Search — log search vs Elasticsearch

Web/application log lines (the workload Manticore is built for: full-text search with
filters and aggregations) loaded into Manticore Search 29.9.0 and Elasticsearch 9.5.4 on the
same machine with the same CPU and memory caps, then the same queries on both. A Go program
([`app/`](app)) generates the data, loads it, checks that both engines return the same counts,
and measures ingest rate, size and query latency.

## Quick start

```bash
make up      # Manticore and Elasticsearch, each capped at 4 CPUs / 4 GB, wait until healthy
make run     # build app/, run it (DOCS=2000000 by default), print sizes and container memory
make status  # containers, tables / indices
make cli     # mysql client on Manticore
make down    # remove containers, volumes and the built image
```

`make run DOCS=1000000 RUNS=200 CLIENTS=4 WRITERS=8 BATCH=5000` (all optional); `ENGINES=manticore`
runs one engine. Output is also written to `results/run-<UTC time>.txt`.

## Setup

| service | image | host port | limits | notes |
|---|---|---|---|---|
| `manticore` (`manticore-ls`) | `manticoresearch/manticore:29.9.0` | `127.0.0.1:9336` (SQL) | `cpus: 4`, `mem_limit: 4g` (`ENGINE_CPUS`, `ENGINE_MEM`) | `auto_schema` off |
| `elasticsearch` (`manticore-ls-elasticsearch`) | `elastic/elasticsearch:9.5.4` | `127.0.0.1:9201` | same | single node, security off, heap 2 GB (`ES_HEAP`), disk watermarks off (shared, nearly full Docker VM disk) |
| `app` (profile `run`) | built from [`app/Dockerfile`](app/Dockerfile) (Go 1.26) | – | – | `go-sql-driver/mysql` 1.10.1 for Manticore (MySQL protocol), official `go-elasticsearch/v9` 9.5.2 |

Both images are arm64 native.

## What `make run` does

1. **Data.** Each log line is generated from its id (same documents for both engines):
   timestamp over 7 days, service (10), method, path (10), status, bytes, latency, level,
   client IP, and a message like `GET /api/v1/payments 502 connection refused by upstream redis on port 4242`
   from 28 templates (info / warn / error) with numbers and words filled in.
2. **Ingest.** 8 writers, 5,000 documents per request, one engine after the other.
   Manticore: multi-row `INSERT` into an RT table with columnar attributes
   (`engine='columnar'`). Elasticsearch: `_bulk` into an index with 1 primary shard, 0 replicas,
   `text` for the message and `keyword` / numeric / `date` / `ip` fields for the rest.
   "Settled" adds what makes all data searchable and on disk: Manticore `FLUSH RAMCHUNK` and
   waiting for background merges; Elasticsearch `_refresh` + `_flush`.
3. **Size** as each engine reports it, plus `du` of the data directory and `docker stats` memory.
4. **Correctness.** One fixed variant of every query is run on both engines; hit counts (and
   every bucket of the aggregations) must equal the count the program computes from its own
   generator. Exits non-zero otherwise.
5. **Latency.** Each query runs 200 times from 4 concurrent clients with randomized
   parameters (word, phrase, 1-hour window, status), the same sequence for both engines, after
   8 warm-up runs. Elasticsearch requests use `request_cache=false` (otherwise repeated
   aggregations come from the shard request cache; Manticore's query cache only keeps queries
   slower than 3 s by default).

| query | Manticore (SQL) | Elasticsearch (query DSL) |
|---|---|---|
| one word, top 20 by relevance | `MATCH('timeout') LIMIT 20` + `SHOW META` `total_found` | `match` + `track_total_hits` |
| phrase, top 20 | `MATCH('"connection refused"')` | `match_phrase` |
| word + `status>=500` + 1-hour window, newest 20 | `MATCH() AND status>=500 AND ts>=… AND ts<… ORDER BY ts DESC` | `bool` `must` `match`, `filter` `range` x2, `sort` `ts` |
| errors per service, 1 day | `WHERE status>=500 AND ts … GROUP BY service` | `range` filters + `terms` agg |
| word + count per status | `MATCH() GROUP BY status` | `match` + `terms` agg on `status` |
| requests per service for one path and status | `WHERE path='/api/v1/payments' AND status=… GROUP BY service` | `term` filters + `terms` agg |

## Results

2026-10-04, one run, `make run DOCS=1000000` (1M documents, 7 days), Apple M4 Pro (Docker VM:
11 CPUs, 24.4 GB, aarch64), Docker 29.5.3, each engine capped at 4 CPUs / 4 GB, client
uncapped. Shared Docker VM (other agents' containers running, VM disk 96–99% full); 1M
documents instead of the 2M default because of the disk.

**Correctness: all 6 queries matched the generator on both engines** (e.g. `timeout` 11,212
hits, `"connection refused"` 10,990, errors per service over a day 15,609 in 10 buckets).

| ingest, 1M docs | Manticore | Elasticsearch | ratio |
|---|--:|--:|--:|
| acknowledged | 5.9 s, 169,661 docs/s | 8.3 s, 120,180 docs/s | Manticore 1.41x |
| searchable and settled | 6.8 s, 147,157 docs/s | 8.5 s, 117,812 docs/s | Manticore 1.25x |
| request (5,000 docs) p50 / p99 | 221 ms / 359 ms | 236 ms / 1,715 ms | p99: Manticore 4.8x lower |
| size reported by the engine | `disk_bytes` 203,201,951 B (7 disk chunks) | store 148,349,123 B (28 segments) | Elasticsearch 1.37x smaller |
| data directory (`du`) | 194 MB | 142 MB | Elasticsearch 1.37x smaller |
| container memory (`docker stats`) | 472.8 MiB (`ram_bytes` 5,235,528 B) | 2.542 GiB (JVM heap used 826,641,056 B of 2 GiB) | Manticore 5.5x less |

| query (200 runs, 4 clients) | Manticore q/s | p50 ms | p99 ms | Elasticsearch q/s | p50 ms | p99 ms | q/s ratio | p50 ratio |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| one word, top 20 | 2,966 | 1.1 | 5.2 | 703 | 3.6 | 48.6 | 4.2x | 3.3x |
| phrase, top 20 | 1,272 | 1.9 | 51.1 | 627 | 3.2 | 51.7 | 2.0x | 1.7x |
| word + status + 1 h, newest 20 | 4,710 | 0.8 | 1.5 | 1,001 | 2.4 | 41.7 | 4.7x | 3.0x |
| errors per service, 1 day | 2,811 | 1.3 | 3.0 | 408 | 4.9 | 49.7 | 6.9x | 3.8x |
| word + count per status | 2,386 | 1.5 | 4.1 | 1,193 | 2.3 | 40.5 | 2.0x | 1.5x |
| one path + status, per service | 3,983 | 0.9 | 2.1 | 798 | 3.0 | 50.5 | 5.0x | 3.3x |

Ratios are Manticore over Elasticsearch (q/s) and Elasticsearch over Manticore (p50).

- Elasticsearch p99: 40.5–51.7 ms on every query; Manticore p99: 1.5–5.2 ms, except the phrase
  query (51.1 ms). Not investigated; one run on a shared VM. With 200 runs, p99 is the
  2nd-slowest run.
- Disk: Manticore stores the text fields (`stored` by default) and had 7 unmerged disk chunks;
  Elasticsearch compresses `_source`.

## Design notes

- **Inverted index plus columnar attributes in one table.** `MATCH()` uses the full-text
  index; filters, sorts and `GROUP BY` read only the needed attribute columns (Manticore
  Columnar Library) with secondary indexes on them, in the same pass.
- **No refresh interval.** Manticore RT tables are searchable when `INSERT` returns (RAM chunk
  + binlog); Elasticsearch makes new documents visible on refresh (1 s default; the program
  calls `_refresh` once at the end).
- **SQL over the MySQL protocol.** Any MySQL client or driver works; full-text is a `MATCH()`
  predicate. The same queries are available as JSON over HTTP (`/search`), and there is an
  Elasticsearch-like `_bulk` endpoint.
- **No JVM.** Memory is the OS page cache plus what searchd allocates; Elasticsearch reserves
  its heap up front (here 2 GB).
- **Methodology** follows part of [db-benchmarks.com](https://db-benchmarks.com/) (its framework,
  [db-benchmarks/db-benchmarks](https://github.com/db-benchmarks/db-benchmarks), also runs
  Manticore's own nightly benchmarks): same CPU/RAM limits per engine via Docker, internal
  result caches off (`request_cache=false`; Manticore's query cache does not keep fast
  queries), warm-up runs, and `count` checks so both engines answer over the same data. Not
  done here: purging the OS cache and restarting the engine before cold runs, fixed CPU
  frequency, and repeating until the coefficient of variation is low. Its `logs10m` test
  (10M Nginx log lines) is the larger version of this workload.

## Known issues

- **Elasticsearch `range` on a `date` field with `epoch_second` mapping**: numbers in the
  query are read as epoch **milliseconds** unless the query also says
  `"format": "epoch_second"`. Without it the time-window queries returned 0 hits (the
  correctness check caught it).
- **Elasticsearch on a nearly full disk**: at 95% (flood stage) it makes indices read-only;
  the compose file turns the disk thresholds off because the shared Docker VM disk was 96–99%
  full.
- **Manticore on a full disk**: `INSERT` fails with
  `unable to write to binlog: /var/lib/manticore/binlog/binlog.0000: write error: No space left on device`.
- `docker compose run` recreates `manticore` / `elasticsearch` if their settings differ from
  `make up` (e.g. a different `ENGINE_MEM`), which empties nothing (named volumes) but restarts
  them; pass the same variables to both commands.

## Links

- Manticore full-text operators: https://manual.manticoresearch.com/Searching/Full_text_matching/Operators
- Elasticsearch `range` query `format`: https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-range-query
- db-benchmarks: https://db-benchmarks.com/ and https://github.com/db-benchmarks/db-benchmarks
- Elasticsearch in this repo (7.12 Compose cluster): [`../../elasticsearch`](../../elasticsearch)
