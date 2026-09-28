# Neo4j — single node

One Neo4j Community Edition server (512 MiB heap), Bolt on `localhost:7687` and
Neo4j Browser on http://localhost:7474.

```bash
make up       # start and wait for cypher-shell to answer
make test     # run cypher/*.cypher: constraint + index, MERGE, paths, shortestPath, aggregation
make status   # SHOW DATABASES
make cli      # interactive cypher-shell
make benchmark  # load + query a seeded social graph (SMOKE=1 for 10k persons), see below
make down     # remove the container and its volume
```

- Bolt: `bolt://localhost:7687`, HTTP/Browser: http://localhost:7474
- Credentials: `neo4j` / `demo-password` (fixed demo value, set via `NEO4J_AUTH`)

Community Edition has one user database (`neo4j`, plus `system`) and no clustering, RBAC or
node-key/existence constraints; uniqueness constraints and indexes work.

## Benchmark

`make benchmark` (after `make up`) runs [`bench/bench.py`](bench/bench.py) with the official
`neo4j` Python driver in the pinned uv image `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`
(deps locked in `bench/uv.lock`, no host Python needed; the uv cache and venv sit in the
`uv-cache` volume, which `make down` keeps: `docker volume rm neo4j-single_uv-cache`).
The workload is identical in [`../../arrangodb/single-node`](../../arrangodb/single-node)
and [`../../nebula-graph/single-node`](../../nebula-graph/single-node), so results
compare:

1. **Generate** (seeded, in Python): N persons with `handle`, `name`, `age`, `city`
   (default 100k; `SMOKE=1` uses 10k; or `make benchmark N=...`) and ~10 `follows` edges
   each, power-law-ish: Pareto out-degree (min 5) and targets drawn by skewed popularity,
   so a few persons have thousands of followers.
2. **Bulk load**, one client, as `(:BenchPerson)-[:BENCH_FOLLOWS]->(:BenchPerson)` in the
   `neo4j` database (Community has no second user database): persons, then relationships,
   in batches of 10,000 rows through `UNWIND $rows ... CREATE`, one write transaction per
   batch; uniqueness constraints on `id` and `handle` exist during the load. Reported as
   rows/s.
3. **Queries**, every answer checked against the value computed in Python from the
   generated graph (`check` column):
   - point lookup by the indexed `handle`: p50/p95/p99 and QPS with 1 and 8 clients
     (separate processes, one connection each)
   - 1-hop count (out-degree) and 2-hop count (distinct persons within 2 hops)
   - directed `shortestPath(... [:BENCH_FOLLOWS*..6] ...)` between random pairs (latency,
     % found)
   - top-10 most-followed persons: `COUNT { (p)<-[:BENCH_FOLLOWS]-() }` per person
4. **Clean up**: the bench nodes and relationships are deleted in batches (`CALL { DETACH
   DELETE } IN TRANSACTIONS`) and the constraints dropped, so `make test` still works
   afterwards.

Results print as a table and go to `results/neo4j-<timestamp>.json` (git-ignored) with
the server and client versions, N, edge count and the Docker VM's CPU count and memory.

What it shows: point lookups and 1-2 hop counts are index / adjacency hits in all three
engines, so their latency is mostly client and protocol round trip; the engines differ in
bulk load throughput, in shortest path (traversal engine) and in the full-graph
aggregation. Neo4j stores relationships as linked records per node (index-free adjacency),
so traversals and shortest path are its home ground, and the top-10 reads each node's
stored degree instead of scanning edges. Transactional `UNWIND` is the online load path;
`neo4j-admin database import` (offline, into an empty database) is much faster but not
usable here. The heap (512 MiB) and page cache (256 MiB) from `docker-compose.yml` are
left as they are.

### Sample results

TODO: fill in from a quiet machine (`make benchmark`, N=100k, ~950k edges).

| metric | Neo4j |
| --- | --- |
| load persons (rows/s) | TODO |
| load follows (rows/s) | TODO |
| lookup by handle, 1 client: QPS / p99 ms | TODO |
| lookup by handle, 8 clients: QPS / p99 ms | TODO |
| 1-hop count p50 ms | TODO |
| 2-hop count p50 ms | TODO |
| shortest path p50 ms (% found) | TODO |
| top-10 most-followed p50 ms | TODO |
