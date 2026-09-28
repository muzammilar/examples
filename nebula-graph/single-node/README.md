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

**Resource budget.** For the run [`bench/limits.sh`](bench/limits.sh) splits `BENCH_CPUS=4` /
`BENCH_MEM=6g` (no swap) with `docker update`: `storaged` 2 CPUs / 3.5 GB (the data path),
`graphd` 1.5 CPUs / 2 GB (query planning and execution), `metad` 0.5 CPU / 512 MB. The old
limits come back afterwards. Docker cannot remove a memory limit from a running container, so
"unlimited" returns as the Docker VM's total memory; `make down && make up` starts clean. The
bench client has `cpus: 2` in compose (`BENCH_CLIENT_CPUS`). The JSON records the applied limits
under `limits`. The daemons size their worker and IO thread pools from the host's cores at
startup (the whole VM here), so they run more threads than they have CPU quota, and the cgroup
caps the CPU time. `storaged` was the busy one, at its 2-CPU cap.

### Sample results

2026-09-28, `make benchmark` (N=100k, 952,309 edges), Docker Desktop 29.5.3 on an Apple M4 Pro
(Docker VM: 11 CPUs, 24.4 GB, aarch64, native images), NebulaGraph 3.8.0, 4 CPUs / 6 GB split
as above, client 2 CPUs.

| metric | NebulaGraph |
| --- | --- |
| load persons (rows/s) | 9,489 |
| load follows (rows/s) | 173,136 |
| lookup by handle, 1 client: QPS / p99 ms | 1,790 / 0.68 |
| lookup by handle, 8 clients: QPS / p99 ms | 4,373 / 56.1 |
| 1-hop count p50 ms | 0.51 |
| 2-hop count p50 ms | 0.93 |
| shortest path p50 ms (% found) | 2.50 (88%) |
| top-10 most-followed p50 ms | 3,490 |

Edges load fastest of the three graph examples (173k/s), and adjacency hops stay sub-millisecond.
Vertex inserts that maintain the `handle` tag index are slow (9.5k/s), though. Global work is
Nebula's weak spot: shortest path is 2-4x slower than the other two, and the top-10 aggregation
over an edge-index scan takes 3.5 s, against ~0.1 s elsewhere. The 8-client p99 again reflects
8 client processes on 2 CPUs.
