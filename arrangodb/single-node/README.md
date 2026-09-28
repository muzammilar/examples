# ArangoDB — single node

One ArangoDB server (single-server mode), web UI and HTTP API on `localhost:8529`.
`make up` creates database `demo` with `people`, `cities`, a `knows` edge collection,
a persistent index and a named graph `social`.

```bash
make up       # start, wait for the healthcheck, create schema (aql/schema.js)
make test     # run aql/*.aql: inserts, edges, UPSERT, indexed filter, traversal, shortest path
make status   # server version and availability over HTTP
make cli      # interactive arangosh on database demo
make benchmark  # load + query a seeded social graph (SMOKE=1 for 10k persons), see below
make down     # remove the container and its volume
```

- HTTP API / web UI: http://localhost:8529 (user `root`, password `demo` — demo only)

Queries are idempotent (`overwriteMode: "replace"`), except `UPSERT`, which bumps
the visit counters on every run.

Since 3.12.5 there is one image for all editions; it reports `license: enterprise`
but runs under the ArangoDB Community License (free, 100 GiB dataset limit).

## Benchmark

`make benchmark` (after `make up`) runs [`bench/bench.py`](bench/bench.py) with the official
`python-arango` client in the pinned uv image `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`
(deps locked in `bench/uv.lock`, no host Python needed; the uv cache and venv sit in the
`uv-cache` volume, which `make down` keeps: `docker volume rm arangodb-single_uv-cache`).
The workload is identical in [`../../neo4j/single-node`](../../neo4j/single-node) and
[`../../nebula-graph/single-node`](../../nebula-graph/single-node), so results compare:

1. **Generate** (seeded, in Python): N persons with `handle`, `name`, `age`, `city`
   (default 100k; `SMOKE=1` uses 10k; or `make benchmark N=...`) and ~10 `follows` edges
   each, power-law-ish: Pareto out-degree (min 5) and targets drawn by skewed popularity,
   so a few persons have thousands of followers.
2. **Bulk load**, one client, into a separate database `bench`: persons, then follows,
   in batches of 10,000 through `import_bulk` (`POST /_api/import`); a unique persistent
   index on `handle` exists during the load. Reported as rows/s.
3. **Queries**, every answer checked against the value computed in Python from the
   generated graph (`check` column):
   - point lookup by the indexed `handle`: p50/p95/p99 and QPS with 1 and 8 clients
     (separate processes, one connection each)
   - 1-hop count (out-degree) and 2-hop count (distinct persons within 2 hops)
   - directed `SHORTEST_PATH` between random pairs, capped at 6 hops (latency, % found)
   - top-10 most-followed persons: `COLLECT` over every edge
4. **Clean up**: database `bench` is dropped, so `make test` still works afterwards.

Results print as a table and go to `results/arangodb-<timestamp>.json` (git-ignored) with
the server and client versions, N, edge count and the Docker VM's CPU count and memory.

What it shows: point lookups and 1-2 hop counts are index / edge-index hits in all three
engines, so their latency is mostly client and protocol round trip; the engines differ in
bulk load throughput, in shortest path (traversal engine) and in the full-graph
aggregation, which ArangoDB answers by scanning the edge collection (Neo4j reads stored
degrees instead). ArangoDB's document-store roots show in the load and lookup numbers.

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `arangodb`
container at `BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update`, and restores the
old limits afterwards. Docker cannot remove a memory limit from a running container, so
"unlimited" comes back as the Docker VM's total memory; `make down && make up` starts clean.
The bench client has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records the applied
limits under `limits`. ArangoDB sizes its RocksDB block cache and scheduler threads at startup
from the memory and cores it detects, which is the whole VM here, since the cap comes later. At
this size it used ~0.75 GB. Under the budget, the client rather than the server is the limit: it
sat at its 2 CPUs while the server used about one.

### Sample results

2026-09-28, `make benchmark` (N=100k, 952,309 edges), Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64, native image), ArangoDB 3.12.12 capped at 4 CPUs /
6 GB, client 2 CPUs.

| metric | ArangoDB |
| --- | --- |
| load persons (rows/s) | 125,492 |
| load follows (rows/s) | 119,112 |
| lookup by handle, 1 client: QPS / p99 ms | 2,386 / 0.74 |
| lookup by handle, 8 clients: QPS / p99 ms | 4,679 / 57.0 |
| 1-hop count p50 ms | 0.42 |
| 2-hop count p50 ms | 0.47 |
| shortest path p50 ms (% found) | 1.09 (88%) |
| top-10 most-followed p50 ms | 148 |

Loading and edge-index hops are fast: 1-hop, 2-hop and shortest path all finish in about a
millisecond. Aggregating over every edge (top-10 most-followed) is a full scan at ~150 ms. The
8-client p99 of 57 ms comes from 8 client processes sharing 2 client CPUs.
