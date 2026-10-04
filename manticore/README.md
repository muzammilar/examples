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

JSON API. Replication is synchronous multi-master (Galera); auto-sharded tables
(`shards= rf=`) arrived in 27.1.5. Open source (GPL-3.0; the columnar library is Apache-2.0).

| folder | what |
|---|---|
| [`docker-compose-cluster/`](docker-compose-cluster) | 3-node replication cluster with a replicated table and an auto-sharded table (6 shards, rf=2). `make failover` kills a node under a Go load and counts every acknowledged row; `make scale-out` / `make scale-in` go 3 → 5 → 3 nodes under load. |

## Benchmark

Apple M4 Pro, Docker VM aarch64, shared VM, Manticore 29.9.0, no CPU/memory caps.

| example | date | setup | result |
|---|---|---|---|
| [docker-compose-cluster](docker-compose-cluster/README.md#failover) | 2026-10-04 | Go client, 4 writers (`REPLACE` batches of 200 into both tables), 4 readers; `docker kill` one of 3 nodes for ~27 s | 71–105k rows/s before the kill; no write acknowledged for ~18 s; replicated table: 0 of 1.14M acknowledged rows lost; sharded table: 3,532 lost and the nodes disagree afterwards (two runs) |
| [docker-compose-cluster](docker-compose-cluster/README.md#scale-out-and-in-3--5--3) | 2026-10-04 | 3 → 5 → 3 nodes, writes paced at 1,000 rows/s per table | joins synced in 1.8–2.7 s; shard layout settled in 11–17 s; replicated table 0 lost in both steps; sharded table lost 10,139 of 100,800 rows on scale-out (the fifth node got no shard), 0 on scale-in |

Full-text search engine (C++, a fork of Sphinx): SQL over the MySQL protocol, HTTP JSON API,
synchronous multi-master replication (Galera). GPL-3.0; columnar library Apache-2.0.

| folder | what |
|---|---|
| [`kubernetes-helm/`](kubernetes-helm) | Official Helm chart 29.9.1 on kind: 3 workers + read balancer, failover (force-delete a worker pod under load), scale 3 → 5 → 3 workers under load. No official operator exists. |

## Benchmark

Apple M4 Pro, Docker VM aarch64, kind, Manticore 29.9.0.1.

| example | date | setup | result |
|---|---|---|---|
| [kubernetes-helm failover](kubernetes-helm/README.md#failover) | 2026-10-04 | Go load, 4 writers (`REPLACE` x 200 rows) + 4 readers on 3 workers; worker-1 force-deleted at 15 s | 81,473–89,961 rows/s before; 2,900 rows/s at the low point; back to 72,026 rows/s 13 s after the delete; new pod ready in 11.0 s; 3,788,600 rows acknowledged, 0 lost |
| [kubernetes-helm scaling](kubernetes-helm/README.md#scale-out-and-in-3--5--3) | 2026-10-04 | `kubectl scale` 3 → 5 → 3, writes paced at 1,000 rows/s | scale-out 148.4 s, scale-in 4.9 s; 0 failed writes; 0 of 160,200 / 46,000 rows lost |

JSON API; built for full-text search with filters and aggregations (logs, e-commerce, site
search). Open source (GPL-3.0; the columnar library is Apache-2.0).

| folder | what |
|---|---|
| [`log-search/`](log-search) | Web/app log lines in Manticore and Elasticsearch 9.5.4 side by side (same caps, same data, same queries) from a Go program: ingest rate, disk and memory, full-text / phrase / filtered / aggregation latency, and a check that both return the generator's exact counts. |

## Benchmark

Apple M4 Pro, Docker VM aarch64, shared VM.

| example | date | setup | result |
|---|---|---|---|
| [log-search](log-search/README.md#results) | 2026-10-04 | 1M log lines, 8 writers x 5,000 docs; 6 queries x 200 runs from 4 clients; Manticore 29.9.0 and Elasticsearch 9.5.4 each 4 CPUs / 4 GB | Manticore vs Elasticsearch: ingest 169,661 vs 120,180 docs/s (1.41x); disk 194 MB vs 142 MB (Elasticsearch 1.37x smaller); container memory 472.8 MiB vs 2.542 GiB (5.5x less); query q/s 2.0x–6.9x higher, p50 0.8–1.9 ms vs 2.3–4.9 ms (1.5x–3.8x), p99 1.5–5.2 ms (phrase 51.1 ms) vs 40.5–51.7 ms; counts identical on all 6 queries |

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

Seen with Manticore 29.9.0 (Buddy 4.4.3), 2026-10-04. Details in each example:

| issue | example |
|---|---|
| Auto-sharding times out (`Waiting timeout exceeded.`) with the Docker image's default `listen` order; put `$ip:9312` first | [docker-compose-cluster](docker-compose-cluster/README.md#known-issues) |
| Sharded tables lose acknowledged writes and diverge between nodes when a node fails and rejoins, or when shards move on scale-out | [docker-compose-cluster](docker-compose-cluster/README.md#failover) |
| With `auto_schema` on (default), an `INSERT` during a shard rebalance created a local RT table named like the sharded table | [docker-compose-cluster](docker-compose-cluster/README.md#failover) |
| Scale-out to 5 nodes left the fifth node without shards and without the sharded table | [docker-compose-cluster](docker-compose-cluster/README.md#scale-out-and-in-3--5--3) |
| A full disk makes Galera abort the node (`Node consistency compromised, aborting...`) | [docker-compose-cluster](docker-compose-cluster/README.md#known-issues) |

Chart 29.9.1, 2026-10-04. Details in [kubernetes-helm](kubernetes-helm/README.md#known-issues):

| issue |
|---|
| Workers OOMKilled (1Gi limit) during state transfer to a new worker at ~10.5M rows; the chart sets no memory limit by default |
| `autoAddTablesInCluster` only adds tables that exist when a worker starts |
| Scale-in leaves removed pods in `cluster_…_nodes_set`; their PVCs stay |
| Full disk: a joining worker cannot allocate its 128 MiB `galera.cache` and loops on `must reinit` |
| First install takes 295–326 s (60 s wait on worker-0, workers start one at a time) |

Seen 2026-10-04. Details in [log-search](log-search/README.md#known-issues):

- Elasticsearch reads numbers in a `range` query on an `epoch_second` date field as milliseconds unless the query sets `"format": "epoch_second"`.
- On a full disk Manticore rejects `INSERT` (`unable to write to binlog ... No space left on device`); Elasticsearch goes read-only at its 95% flood-stage watermark.
