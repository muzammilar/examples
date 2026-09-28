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

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) caps the `neo4j`
container at `BENCH_CPUS=4` / `BENCH_MEM=6g` (no swap) with `docker update`, and restores the
old limits afterwards. Docker cannot remove a memory limit from a running container, so
"unlimited" comes back as the Docker VM's total memory; `make down && make up` starts clean.
The bench client has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records the applied
limits under `limits`. Neo4j's own settings bind memory before the cgroup cap does: 512 MB heap
and 256 MB page cache, per `docker-compose.yml`, so it peaked at ~1.2 GB. The JVM sized its GC
and worker threads from the VM's 11 CPUs at startup, since the cap is applied later.

### Sample results

2026-09-28, `make benchmark` (N=100k, 952,309 edges), Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64, native image), Neo4j 2026.09.0 Community capped at
4 CPUs / 6 GB, client 2 CPUs.

| metric | Neo4j |
| --- | --- |
| load persons (rows/s) | 56,875 |
| load follows (rows/s) | 78,249 |
| lookup by handle, 1 client: QPS / p99 ms | 1,959 / 1.50 |
| lookup by handle, 8 clients: QPS / p99 ms | 7,398 / 4.40 |
| 1-hop count p50 ms | 0.36 |
| 2-hop count p50 ms | 0.43 |
| shortest path p50 ms (% found) | 0.70 (88%) |
| top-10 most-followed p50 ms | 107 |

Neo4j scales best under concurrency of the three graph examples (7.4k QPS with 8 clients,
p99 4.4 ms). It has the fastest shortest path and whole-graph aggregation. Bulk loading through
`UNWIND` batches is the slowest part, at about half ArangoDB's rate.
