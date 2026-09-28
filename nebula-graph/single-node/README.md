# NebulaGraph — single node

The smallest working NebulaGraph cluster: one `metad` (metadata/schema), one
`storaged` (data), one `graphd` (query engine), plus `nebula-console` as a
`tools` profile service. `make up` registers storaged with metad (`ADD HOSTS`).

```bash
make up       # start, register storaged (ADD HOSTS), wait until it is ONLINE
make test     # run ngql/*.ngql: space, tag/edge/index, inserts, GO, LOOKUP, MATCH, FIND PATH
make status   # SHOW HOSTS
make cli      # interactive nebula-console
make benchmark  # load + query a seeded social graph (SMOKE=1 for 10k persons), see below
make down     # remove containers and volumes
```

- Graph service: `localhost:9669`, user `root`, password `nebula` (auth is off by default; any password works)
- metad / storaged are only reachable inside the compose network (9559 / 9779)

`--heartbeat_interval_secs=2` (default 10) so new spaces and schema propagate quickly;
`ngql/01-schema.ngql` still sleeps after `CREATE SPACE` and after the DDL, because
graphd and storaged only see them after a few heartbeats. `replica_factor = 1`
since there is one storaged. Service logs are files under `/usr/local/nebula/logs`
in each container, not `docker logs`.

The first storage call on a fresh graphd connection can take ~5 s while graphd
opens its storage client connections.

## Benchmark

`make benchmark` (after `make up`) runs [`bench/bench.py`](bench/bench.py) with the official
`nebula3-python` client in the pinned uv image `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim`
(deps locked in `bench/uv.lock`, no host Python needed; the uv cache and venv sit in the
`uv-cache` volume, which `make down` keeps:
`docker volume rm nebula-graph-single_uv-cache`).
The workload is identical in [`../../arrangodb/single-node`](../../arrangodb/single-node)
and [`../../neo4j/single-node`](../../neo4j/single-node), so results compare:

1. **Generate** (seeded, in Python): N persons with `handle`, `name`, `age`, `city`
   (default 100k; `SMOKE=1` uses 10k; or `make benchmark N=...`) and ~10 `follows` edges
   each, power-law-ish: Pareto out-degree (min 5) and targets drawn by skewed popularity,
   so a few persons have thousands of followers.
2. **Bulk load**, one client, into a separate space `bench` (10 partitions, `vid_type =
   INT64`, person id = vid): persons, then edges, with batched `INSERT VERTEX` / `INSERT
   EDGE` statements of 2,000 rows; the tag index on `handle` and an edge index on
   `follows` exist during the load (`bench.py` waits for them to reach storaged first).
   Reported as rows/s.
3. **Queries**, every answer checked against the value computed in Python from the
   generated graph (`check` column):
   - point lookup by the indexed `handle`: p50/p95/p99 and QPS with 1 and 8 clients
     (separate processes, one connection each)
   - 1-hop count (out-degree) and 2-hop count (distinct persons within 2 hops)
   - directed `FIND SHORTEST PATH ... UPTO 6 STEPS` between random pairs (latency, % found)
   - top-10 most-followed persons: `LOOKUP ON follows` (edge index scan) `| GROUP BY` in
     graphd
4. **Clean up**: space `bench` is dropped, so `make test` still works afterwards.

Results print as a table and go to `results/nebula-graph-<timestamp>.json` (git-ignored)
with
the server and client versions, N, edge count and the Docker VM's CPU count and memory.

What it shows: point lookups and 1-2 hop counts are index / adjacency hits in all three
engines, so their latency is mostly client and protocol round trip; the engines differ in
bulk load throughput, in shortest path (traversal engine) and in the full-graph
aggregation. NebulaGraph separates query (graphd) and storage (storaged), so every query
is at least one extra RPC hop even on one machine, and the top-10 streams every edge from
storaged to graphd; that design pays off when storaged is scaled out across machines,
which this single-node setup cannot show. For large imports the recommended tools are
NebulaGraph Importer / Exchange; batched `INSERT` is the client-side path they use too.

### Sample results

TODO: fill in from a quiet machine (`make benchmark`, N=100k, ~950k edges).

| metric | NebulaGraph |
| --- | --- |
| load persons (rows/s) | TODO |
| load follows (rows/s) | TODO |
| lookup by handle, 1 client: QPS / p99 ms | TODO |
| lookup by handle, 8 clients: QPS / p99 ms | TODO |
| 1-hop count p50 ms | TODO |
| 2-hop count p50 ms | TODO |
| shortest path p50 ms (% found) | TODO |
| top-10 most-followed p50 ms | TODO |
