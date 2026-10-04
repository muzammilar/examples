# Memgraph — single node

One Memgraph Community instance (MAGE image) plus Memgraph Lab on Docker Compose: a Cypher
walkthrough with indexes and constraints, BFS and weighted shortest paths, MAGE algorithms
(PageRank, Louvain community detection, betweenness), a snapshot + WAL crash-recovery check, and
the social-graph benchmark from [`../../neo4j/single-node`](../../neo4j/single-node).

## Quick start

```bash
make up          # sysctl one-shot, Memgraph, Lab; waits until healthy
make test        # cypher/*.cypher, then a duplicate that the unique constraint must reject
make durability  # snapshot, more writes, SIGKILL, restart, count
make benchmark   # bench/bench.py (SMOKE=1 for 10k persons)
make status      # version, storage info, replication role, license info
make cli         # mgconsole in the container
make lab         # prints (and on macOS opens) the Lab URL
make down        # containers, volumes, network
```

## Setup

| service | image | host port | role |
|---|---|---|---|
| `sysctl` | `busybox:1.37` | — | one-shot, privileged: raises `vm.max_map_count` to 524288 if lower |
| `memgraph` | `memgraph/memgraph-mage:3.13.1` | `127.0.0.1:7689` (`MEMGRAPH_PORT`) → 7687 Bolt | database, `--memory-limit=4096` MiB (`MEMGRAPH_MEMORY_LIMIT_MIB`) |
| `lab` | `memgraph/lab:3.13.2` | `127.0.0.1:3010` (`LAB_PORT`) → 3000 | web UI, Quick connect preset to `memgraph:7687` |
| `bench` (profile `bench`) | `ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim` | — | `make benchmark`, `neo4j` Python driver 6.3.1 (`bench/uv.lock`) |

- Host port 7689, not 7687, so it runs next to the Neo4j example. No auth (Community starts with
  no users), so both ports bind to `127.0.0.1`.
- `memgraph-mage` is Memgraph plus the MAGE query modules (C++ and Python). It is 2.97 GB
  (649 MB compressed) against 896 MB for `memgraph/memgraph`, and idled at 783 MiB RSS because
  it loads Python/PyTorch modules at start (`SHOW STORAGE INFO`, `memory_res`).
- Flags: `--telemetry-enabled=false` (on by default), WAL on, snapshot every 60 s (image default
  300 s), snapshot on exit, recovery on startup. The image's other defaults are in
  `/etc/memgraph/memgraph.conf`.
- `vm.max_map_count`: Memgraph logs `Max virtual memory areas vm.max_map_count 262144 is too low,
  increase to at least 524288`. The setting is not namespaced: on Docker Desktop the one-shot
  changes the whole Docker VM until Docker restarts; on Linux it changes the host.

## What it does

| file / target | what |
|---|---|
| `cypher/01-schema.cypher` | label index, label+property index, edge-type index (`CREATE EDGE INDEX ON :ROUTE`), unique and existence constraints; `SHOW INDEX INFO`, `SHOW CONSTRAINT INFO` |
| `cypher/02-create.cypher` | 18 airports in 3 regions and 56 directed `ROUTE` edges with `km`, via `UNWIND` |
| `cypher/03-query.cypher` | index lookup, `*1..2` expansion, `*BFS` (fewest flights), `*WSHORTEST (r, n \| r.km)` (fewest km), `*BFS` with a filter lambda, aggregation, `EXPLAIN` showing `ScanAllByLabelProperties` |
| `cypher/04-mage.cypher` | `pagerank.get()`, `community_detection.get()` (Louvain), `betweenness_centrality.get()`, `weakly_connected_components.get()` |
| `make test` (end) | inserts a duplicate `FRA`; fails unless it gets `Unable to commit due to unique constraint violation on :Airport(code)` |
| `make durability` | 50,000 nodes, `CREATE SNAPSHOT`, 1,000 single-row transactions (WAL only), `docker kill --signal KILL`, start, compare counts |

Sample output (2026-10-04):

- BFS MAD → BKK: `["MAD", "FRA", "DEL", "BKK"]`, 3 flights; WSHORTEST gives the same route, 10,480 km.
- BFS FRA → SIN with `r.km < 8000`: `["FRA", "DEL", "SIN"]` (skips the 10,260 km direct route).
- PageRank top 5: FRA 0.102, HKG 0.0885, SIN 0.0757, BKK 0.066, CDG 0.0618.
- Louvain: 3 communities, exactly the EU / NA / AS regions (6 airports each).
- Betweenness top: FRA 144.2, JFK 80.7, SIN 46.1.
- Durability: 51,000 nodes before SIGKILL, 51,000 after restart, healthy again in 6 s and 7 s (two runs).

Memgraph Lab: `http://127.0.0.1:3010`, Quick connect, then e.g. `MATCH p = ()-[:ROUTE]->() RETURN p`.
Checked only that Lab starts healthy and answers HTTP 200; the browser session was not tested.

## Benchmark

`make benchmark` runs [`bench/bench.py`](bench/bench.py): the workload and harness of
[`neo4j/single-node`](../../neo4j/single-node/README.md#benchmark) (seeded 100k persons,
952,309 `BENCH_FOLLOWS` edges, `UNWIND` batches of 10,000, every answer checked against Python),
with a Memgraph `DB` class at the bottom. Differences in the Memgraph class:

- Index and constraint DDL, and `SHOW VERSION`, run as auto-commit queries: Memgraph refuses them
  in an explicit transaction (`... is not allowed in multicommand transactions`).
- Shortest path is `MATCH (a ...), (b ...) WITH a, b MATCH p = (a)-[:BENCH_FOLLOWS *BFS ..6]->(b)`.
  The `WITH` matters: without it the plan is `BFSExpand` from `a` and a `Filter` on `b`
  (single-source BFS); with it the plan is `STShortestPath` (bidirectional).
- Top-10 uses `inDegree(p)` (the stored degree; only `BENCH_FOLLOWS` edges point at `:BenchPerson`).

Caps: `bench/limits.sh` sets the `memgraph` container to 4 CPUs / 6 GB with `docker update` for the
run and restores it afterwards; the client has 2 CPUs. Same caps as the Neo4j run.

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24.4 GB, Docker 29.5.3), native arm64 images,
Memgraph 3.13.1 capped at 4 CPUs / 6 GB, client 2 CPUs, N=100k / 952,309 edges, one run each,
other agents' containers on the same VM. Neo4j column copied from
[`neo4j/single-node`](../../neo4j/single-node/README.md#sample-results) (2026-09-28, same caps; Neo4j heap 512 MiB, page cache 256 MiB).

| metric | Memgraph 3.13.1 | Neo4j 2026.09.0 Community | Memgraph / Neo4j |
| --- | --- | --- | --- |
| load persons | 79,227 rows/s | 56,875 rows/s | 1.39x |
| load follows | 141,226 rows/s | 78,249 rows/s | 1.80x |
| lookup by handle, 1 client | 5,945 QPS, p99 0.37 ms | 1,959 QPS, p99 1.50 ms | 3.03x QPS, p99 4.1x lower |
| lookup by handle, 8 clients | 12,724 QPS, p99 1.56 ms | 7,398 QPS, p99 4.40 ms | 1.72x QPS, p99 2.8x lower |
| 1-hop count p50 | 0.15 ms | 0.36 ms | 2.4x lower |
| 2-hop count p50 | 0.16 ms | 0.43 ms | 2.7x lower |
| shortest path p50 (found) | 0.29 ms (88%) | 0.70 ms (88%) | 2.4x lower |
| top-10 most-followed p50 | 32.15 ms | 107 ms | 3.3x lower |

- Every answer matched the Python reference (`check` = ok on every row).
- First run, before the `WITH` fix: shortest path p50 77.77 ms / p99 132.22 ms (13 QPS), vs
  p50 0.29 ms with `STShortestPath` (268x). Other rows of that run: load follows 146,816 rows/s,
  8-client lookup 8,231 QPS (vs 12,724 QPS in the run above: single runs on a shared VM vary).
- Memgraph RSS peaked at 1.44 GiB (`peak_memory_res`), including the 783 MiB the MAGE image used idle.
- Raw JSON in `results/` (git-ignored).

## Known issues

- **mgconsole splits input on `;` before parsing comments.** A `;` or a `'` inside a `//` comment
  breaks the file (`mismatched input '<EOF>'`), and a comment at the end of a statement line
  swallows the next statement. Comments go on their own line, without quotes or semicolons.
- **No comment before `EXPLAIN` / `PROFILE`.** `// note` followed by `EXPLAIN MATCH ...` fails
  with `missing DATABASE at 'EXPLAIN'` (the comment is sent with the query and `EXPLAIN` is only
  recognised at the start).
- **`CREATE INDEX ON :ROUTE` makes a label index** named `ROUTE`, not an edge index. Edge-type
  indexes are `CREATE EDGE INDEX ON :ROUTE`.
- **Single-source vs bidirectional BFS** depends on the query shape (see Benchmark).
- **WAL fsync every 100,000 transactions** by default (`--storage-wal-file-flush-every-n-tx`).
  A killed process loses nothing (the data is in the OS page cache, as `make durability` shows);
  a host or VM crash can lose up to that many acknowledged transactions. Set it to 1 for fsync per
  commit.
- MAGE's `*_online` modules (`pagerank_online`, `community_detection_online`, …) need an
  Enterprise license; the log says `Failed to load query module ... because it requires a valid
  enterprise license`. The static versions used here are Community.

## Links

- Docs: https://memgraph.com/docs
- Indexes: https://memgraph.com/docs/fundamentals/indexes
- Deep path traversal (BFS, WSHORTEST): https://memgraph.com/docs/advanced-algorithms/deep-path-traversal
- MAGE: https://memgraph.com/docs/advanced-algorithms/available-algorithms
- Durability: https://memgraph.com/docs/fundamentals/data-durability
- Memgraph Lab: https://memgraph.com/docs/memgraph-lab
