# Manticore Search

Website: https://manticoresearch.com/
GitHub: https://github.com/manticoresoftware/manticoresearch

Full-text search engine (a fork of Sphinx, C++), with SQL over the MySQL protocol and an HTTP
JSON API. Real-time tables, BM25 ranking, columnar storage, secondary indexes and KNN vector
search come in the open-source build (GPLv3; the columnar library is Apache 2.0).

| folder | what |
|---|---|
| [`single-node/`](single-node) | One server on Docker Compose. SQL walkthrough: RT table, full-text (BM25 / BM25F, operators, fuzzy, highlighting), facets, KNN with filters and full-text, updates and transactions; 1M generated rows in a row-wise vs a columnar table with secondary indexes; the HTTP JSON API. `manticore-load` benchmark. |

## Benchmark

Apple M4 Pro, Docker VM aarch64, shared VM. Manticore 29.9.0.

| example | date | setup | result |
|---|---|---|---|
| [single-node](single-node/README.md#benchmark) | 2026-10-04 | `manticore-load`, 500k docs (10–40 words, 5 attributes, 64-dim HNSW vector), columnar RT table, 8 threads; server 4 CPUs / 6 GB | ingest 41,291 docs/s (batch p99 8,263 ms during RAM-chunk flushes); full-text top 20: 1,373–1,387 q/s, p50 1.8–2.0 ms, p99 60.4–60.5 ms; full-text + filter + group by 1,596 q/s; KNN top 10: 127 q/s, p50 76 ms (part of the vectors still in the RAM chunk) |
| [single-node](single-node/README.md#row-wise-vs-columnar-1m-rows-from-make-test) | 2026-10-04 | `make test`, 1M generated log rows, no caps | ingest row-wise 452,628 docs/s vs columnar 373,456 docs/s (1.21x); `ram_bytes` 45,198,376 B vs 1,678,376 B (26.9x less columnar) at 180 MB vs 178 MB on disk; secondary-index lookup p50 0.2 ms on both |

## Known issues

Seen with Manticore 29.9.0 (Buddy 4.4.3), 2026-10-04. The project is active (29.9.0 on
2026-09-11, dev builds daily). Details in each example's Known issues:

| issue | example |
|---|---|
| `KNN(field, k, <doc id>)` in upper case fails to parse; lower case works | [single-node](single-node/README.md#known-issues) |
| `k` in `knn()` is per disk chunk and ignored for the RAM chunk; use `LIMIT` | [single-node](single-node/README.md#known-issues) |
| Optimizer hints only at the end of the statement | [single-node](single-node/README.md#known-issues) |
| `UPDATE` takes constants only; no `ORDER BY` on an MVA facet | [single-node](single-node/README.md#known-issues) |
| Fuzzy search misses words whose stemmed form is more than 2 edits away | [single-node](single-node/README.md#known-issues) |
| After a disk-full error, `DROP TABLE` leaves the directory and re-`CREATE` fails | [single-node](single-node/README.md#known-issues) |
