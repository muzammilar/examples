# Manticore Search — single node

One Manticore Search server from the official image `manticoresearch/manticore:29.9.0`
(override with `MANTICORE_VERSION`), data in a named volume. SQL walkthrough over the MySQL
protocol (real-time tables, full-text with BM25 and highlighting, facets, KNN vector search,
columnar storage and secondary indexes), the HTTP JSON API, and a `manticore-load` benchmark.

## Quick start

```bash
make up         # start and wait until searchd and Buddy answer
make test       # sql/*.sql, then scripts/07-columnar.sh (1M generated rows) and scripts/08-http.sh
make benchmark  # manticore-load ingest + queries from a second container; results/
make status     # container, versions, tables
make cli        # mysql client inside the container
make down       # remove the container, the volume and anything built
```

## Setup

| service | image | port (host) | role |
|---|---|---|---|
| `manticore` (`manticore-single`) | `manticoresearch/manticore:29.9.0` (arm64 native) | `127.0.0.1:9306` (`MANTICORE_MYSQL_PORT`) | SQL over the MySQL protocol |
| | | `127.0.0.1:9308` (`MANTICORE_HTTP_PORT`) | HTTP: `/sql`, `/search`, `/insert`, `/update`, `/delete`, `/bulk`, `/cli` |
| `bench` (profile `bench`) | same image | – | runs [`bench/run.sh`](bench/run.sh) with `manticore-load`, `cpus: 4` |

- The image includes the Manticore Columnar Library 14.2.1 (columnar storage, secondary
  indexes, KNN, embeddings) and Manticore Buddy 4.4.3 (a PHP sidecar inside the container for
  fuzzy search, `SHOW SHARDING`, autocomplete and the Elasticsearch-like endpoints). The
  health check waits until `SHOW VERSION` lists Buddy, which starts a few seconds after searchd.
- No authentication is configured, so both ports bind to `127.0.0.1`.
- `ulimits`: `nofile` 65536, `memlock` unlimited (as in the image's README).

## What `make test` runs

Each SQL file is echoed (`mysql -v`) with its output.

| step | shows |
|---|---|
| [`01-schema.sql`](sql/01-schema.sql) | RT table `products`: 2 text fields, string/float/int/multi/timestamp attributes, a 4-dim `float_vector` with an HNSW index (cosine), `morphology='stem_en'`, `min_infix_len='3'`; `DESC`, `SHOW CREATE TABLE` |
| [`02-data.sql`](sql/02-data.sql) | 16 products in one `INSERT`; searchable as soon as it returns |
| [`03-full-text.sql`](sql/03-full-text.sql) | `MATCH()` with the default ranker (`proximity_bm25`) vs `ranker=bm25`; BM25F with field weights (`ranker=expr('10000*bm25f(1.2,0.75,{title=5,description=1})')`); operators (`@title`, `-`, `"phrase"`, `"..."~6`, `\|`, `camp*`), infix `*proof*`, `OPTION fuzzy=1` (`runing jaket` → "Waterproof running jacket"); `HIGHLIGHT()` with custom tags and snippet length; full-text + filters + `ANY(tags)`; `SHOW META` with per-keyword docs/hits (stemmed: `run`, `trail`) |
| [`04-facets.sql`](sql/04-facets.sql) | one statement with hits + `FACET category`, `FACET brand`, `FACET INTERVAL(price, 100, 200, 400)`, `FACET tags`; `GROUP BY` with aggregates; best product per category with `WITHIN GROUP ORDER BY` |
| [`05-knn.sql`](sql/05-knn.sql) | `knn(embedding, k, vector)`, "more like document 6" (`knn(embedding, 4, 6)`), KNN + attribute filter (prefiltered inside the HNSW walk), KNN + `MATCH()` |
| [`06-updates.sql`](sql/06-updates.sql) | `UPDATE` (attributes in place), `REPLACE` (whole document, needed for text), `DELETE`, a `BEGIN … COMMIT` transaction |
| [`07-columnar.sh`](scripts/07-columnar.sh) | `manticore-load` writes 1M generated log rows into a row-wise and a columnar table (`engine='columnar'`), then: sizes and per-query plans/latencies (below) |
| [`08-http.sh`](scripts/08-http.sh) | the same table over HTTP JSON: `/insert`, `/search` (bool query with `match` + `range`, `highlight`, `sort`, `aggs` terms), `match_phrase`, `knn`, `/update`, `/sql?mode=raw`, `/delete` |

### Row-wise vs columnar, 1M rows (from `make test`)

`make test` output on 2026-10-04 (Apple M4 Pro, Docker VM aarch64, shared VM, no caps), after
`FLUSH RAMCHUNK` + `OPTIMIZE` (one disk chunk each). Both tables have secondary indexes; the
plan column is `SHOW META LIKE 'index'`. Latencies: 1 thread, 200 runs.

| query | row-wise p50 / p99 ms | plan | columnar p50 / p99 ms | plan |
|---|---|---|---|---|
| `status=503 LIMIT 10` (0.3% of rows) | 0.2 / 1.9 | `status:SecondaryIndex` | 0.2 / 0.3 | `status:SecondaryIndex` |
| same with `/*+ NO_SecondaryIndex(status) */` | 0.5 / 2.2 | full scan | 0.1 / 0.3 | `status:ColumnarScan` |
| `COUNT(*) WHERE status>=500` | 0.1 / 0.3 | `status:Precalc` (answered from the index) | 0.1 / 0.4 | `status:Precalc` |
| `AVG(latency_ms) GROUP BY service` (all rows) | 5.9 / 10.1 | | 3.9 / 5.2 | |
| `status>=500 AND latency_ms>2900` | 0.5 / 2.1 | `status:SecondaryIndex` | 4 / 5 | `latency_ms:ColumnarScan, status:SecondaryIndex` |

| comparison | row-wise | columnar | ratio |
|---|--:|--:|--:|
| `ram_bytes` | 45,198,376 B | 1,678,376 B | 26.9x less with columnar |
| `disk_bytes` | 180,485,698 B | 177,629,265 B | 1.02x |
| ingest, 4 threads x batches of 10,000 | 452,628 docs/s | 373,456 docs/s | 1.21x faster row-wise |
| `GROUP BY service` p50 | 5.9 ms | 3.9 ms | 1.5x faster columnar |
| index lookup + second filter p50 | 0.5 ms | 4 ms | 8x faster row-wise |

- Columnar stores attributes in compressed column blocks on disk and reads only the columns a
  query uses; row-wise attributes are memory-mapped (`ram_bytes`), so filtering the rows an index
  lookup returned on a second attribute is cheaper.

## Benchmark

[`bench/run.sh`](bench/run.sh) runs `manticore-load` (ships with the image) in the `bench`
container against `manticore`:

1. **Ingest**: `DOCS` (500,000) documents into an RT columnar table: random English text of
   10–40 words (`manticore-load`'s 365-word vocabulary), 5 attributes and a 64-dim
   `float_vector` with an HNSW index (L2); 8 threads, batches of 5,000. Then waits until
   background chunk merges finish (`optimizing` = 0).
2. **Queries**, 8 threads, 20,000 each (KNN 5,000): one-word and two-word `MATCH()` (words from
   [`bench/words.txt`](bench/words.txt), the generator's vocabulary, so every word occurs),
   `MATCH()` + `status>=500` + `GROUP BY service`, an attribute-only filter, KNN top 10.
3. Writes `results/manticore-single-<UTC time>.txt` and drops the table (`KEEP=1` keeps it).

```bash
make benchmark                          # defaults above
make benchmark DOCS=200000 THREADS=4
```

Limits: [`bench/limits.sh`](bench/limits.sh) caps `manticore-single` at `BENCH_CPUS=4` /
`BENCH_MEM=6g` with `docker update` and restores the previous limits afterwards. Client:
`cpus: 4` (`BENCH_CLIENT_CPUS`). `manticore-load` generates all batches before the clock
starts, into `/tmp` (a tmpfs in the `bench` container, ~0.9 GB per 1M documents).

### Results

2026-10-04, one run, Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64, native image), Docker
29.5.3, Manticore 29.9.0, server capped at 4 CPUs / 6 GB, client 4 CPUs. The Docker VM was
shared with other agents' workloads (Redpanda, Tarantool) and its disk was 95–99% full.

| phase | total | ops/s | avg ms | p50 | p95 | p99 |
|---|--:|--:|--:|--:|--:|--:|
| ingest, 500k docs (docs/s; latency per 5,000-doc batch) | 500,000 | 41,291 | 1,008 | 325 | 7,975 | 8,263 |
| `MATCH('<1 word>') LIMIT 20` | 20,000 | 1,387 | 5.8 | 1.8 | 50.5 | 60.5 |
| `MATCH('<2 words>') LIMIT 20` | 20,000 | 1,373 | 5.8 | 2.0 | 43.7 | 60.4 |
| `MATCH()` + `status>=500` + `GROUP BY service` | 20,000 | 1,596 | 5.0 | 1.9 | 28.5 | 50.6 |
| `status=X AND latency_ms<100 LIMIT 20` | 20,000 | 1,113 | 7.2 | 2.6 | 49.0 | 68.9 |
| `knn(v, 10, <64 floats>)` | 5,000 | 127 | 63.1 | 76 | 129 | 288 |

Table after ingest: 500,000 documents, `disk_bytes` 135,833,608, `ram_bytes` 372,149,401,
2 disk chunks plus the RAM chunk.

- Ingest batch p95 7,975 ms vs p50 325 ms: batches wait while a full RAM chunk is written as a
  disk chunk and its HNSW graph is built.
- One query word matches ~7% of the documents (25 words per document from a 365-word vocabulary).
- KNN: 127 q/s, p50 76 ms. `ram_bytes` 372,149,401 after ingest: part of the vectors was still in
  the RAM chunk, which has no HNSW index and is searched exhaustively (`k` does not apply to it).

## Known issues

Manticore 29.9.0, Buddy 4.4.3, 2026-10-04.

- **`KNN(...)` in upper case with a document id does not parse**:
  `SELECT ... WHERE KNN(embedding, 4, 6)` → `ERROR 1064 (42000): P01: syntax error, unexpected integer, expecting string or '(' near '6)'`.
  The same call in lower case (`knn(embedding, 4, 6)`) works, as does upper case with a vector.
  The walkthrough uses lower case.
- **`k` in `knn()` is not a result count**: it applies per disk chunk and not to the RAM chunk;
  without `LIMIT` the first query returned all 16 rows. Use `LIMIT` (the manual marks `k` as
  deprecated in JSON).
- **Optimizer hints go after `LIMIT`**: `... WHERE status=503 /*+ NO_SecondaryIndex(status) */ LIMIT 3`
  → `syntax error, unexpected LIMIT`; and `SELECT /*+ ... */ ...` → `unexpected HINT_OPEN`.
  Put the hint at the end of the statement. The `mysql` client strips comments unless started
  with `--comments` (the Makefile passes it).
- **`UPDATE` takes constants only**: `SET stock = stock - 1` → `syntax error, unexpected identifier near 'stock - 1 ...'`.
- **`FACET` on a multi-value attribute cannot be sorted by the value**:
  `FACET tags ORDER BY tags ASC` → `order by MVA is undefined`. Sort by `COUNT(*)`.
- **Fuzzy search compares against the stemmed dictionary**: with `morphology='stem_en'`,
  `MATCH('hedphones') OPTION fuzzy=1` finds nothing (the indexed form is `headphon`, 3 edits
  away); `runing jaket` works.
- **`/sql` with a form body must be URL-encoded**: `--post-data='query=SELECT id, title ...'`
  → `400 Bad Request`. Send the statement as the raw body to `/sql?mode=raw`.
- **Full Docker VM disk**: a benchmark run failed with
  `unable to write to binlog: /var/lib/manticore/binlog/binlog.0001: write error: No space left on device`.
  `DROP TABLE` afterwards left the table directory (341 MB) behind, and the next
  `CREATE TABLE bench` failed with `error adding table 'bench': directory is not empty: /var/lib/manticore/bench`
  (`manticore-load` then dies with `SHOW TABLE STATUS requires an existing table`). Fix: free
  space, remove the directory (`docker exec manticore-single rm -rf /var/lib/manticore/bench`) or `make down`.
- `onnxruntime cpuid_info warning: Unknown CPU vendor. cpuinfo_vendor value: 0` at every start
  on Apple silicon; harmless.

## Links

- Manual: https://manual.manticoresearch.com/
- Docker image: https://github.com/manticoresoftware/docker
- KNN: https://manual.manticoresearch.com/Searching/KNN
- Columnar storage and secondary indexes: https://manual.manticoresearch.com/Creating_a_table/Data_types
- `manticore-load`: https://github.com/manticoresoftware/manticore-load
