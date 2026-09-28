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

### Sample results

TODO: fill in from a quiet machine (`make benchmark`, N=100k, ~950k edges).

| metric | ArangoDB |
| --- | --- |
| load persons (rows/s) | TODO |
| load follows (rows/s) | TODO |
| lookup by handle, 1 client: QPS / p99 ms | TODO |
| lookup by handle, 8 clients: QPS / p99 ms | TODO |
| 1-hop count p50 ms | TODO |
| 2-hop count p50 ms | TODO |
| shortest path p50 ms (% found) | TODO |
| top-10 most-followed p50 ms | TODO |
