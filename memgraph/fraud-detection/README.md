# Memgraph — real-time fraud detection (Go, vs Neo4j)

A payment network (accounts, devices, transfers) with planted fraud rings, run by one Go program
([`app/`](app), `neo4j-go-driver/v6` over Bolt) against Memgraph Community and Neo4j Community
with the same seeded data and the same queries: bulk load, a stream of payment authorizations
(multi-hop risk check, then the insert) from concurrent workers, and graph analytics (ring
detection, shared devices, exposure). At the end the result rows of both engines are compared.

## Quick start

```bash
make up      # Memgraph + Neo4j (same CPU/memory caps), waits until both are healthy
make run     # build the app image and run all phases on Memgraph, then on Neo4j; exits 1 on a mismatch
make build   # build the app image only
make status  # containers, Memgraph memory
make cli     # mgconsole        (make cli-neo4j: cypher-shell)
make down    # containers, volumes, app image
```

`make run ENGINES=memgraph` runs one engine. Sizes: `ACCOUNTS` (50,000), `DEVICES` (20,000),
`TRANSFERS` (300,000), `STREAM` (20,000), `WORKERS` (8), `RUNS` (5 per analytics query). Output
is also written to `results/run-<time>.txt` (git-ignored).

## Setup

| service | container | image | host port | limits |
|---|---|---|---|---|
| `memgraph` | `memgraph-fraud` | `memgraph/memgraph:3.13.1` | `127.0.0.1:7693` | 4 CPUs, 6 GB (`DB_CPUS`, `DB_MEM`), `--memory-limit=5120` |
| `neo4j` | `memgraph-fraud-neo4j` | `neo4j:2026.09.0-community` (as in [`../../neo4j`](../../neo4j)) | `127.0.0.1:7694` | 4 CPUs, 6 GB; heap 2 GiB, page cache 1 GiB |
| `app` (profile `run`) | — | built from [`app/Dockerfile`](app/Dockerfile) (`golang:1.27.1-alpine` → `alpine:3.22`) | — | 3 CPUs (`APP_CPUS`) |
| `sysctl` | one-shot, privileged | `busybox:1.37` | — | `vm.max_map_count` ≥ 524288 (Docker VM / Linux host) |

- Both engines run all the time; the app runs the phases on one engine, then the other, so the
  idle one only holds memory. Memgraph: WAL and periodic snapshots on (image defaults), telemetry off.
- Neo4j: heap 2 GiB, page cache 1 GiB, enough to cache the whole graph.

## What `make run` does

Data (seed 42, generated in the app): 50,000 accounts (200 `flagged`), 20,000 devices, 54,893
`USES` (10% of accounts have a second device), 300,186 initial `TRANSFER`s (0.5% ≥ 5,000; receivers
skewed so low ids receive most), 40 planted rings of 3–5 accounts whose transfers are all ≥ 5,000,
and 10 more rings whose closing transfer arrives in the stream.

| phase | queries |
|---|---|
| 1. load | `UNWIND $rows ... CREATE` in batches of 10,000: accounts, devices, `USES`, `TRANSFER`s (unique constraints on `Account.id`, `Device.id`, index on `Account.flagged`) |
| 2. stream | 20,000 transfers from 8 workers. Per transfer: risk check `MATCH (d:Account {id: $dst}) OPTIONAL MATCH (d)-[:USES]->(:Device)<-[:USES]-(f:Account {flagged: true}) ... OPTIONAL MATCH (d)-[:TRANSFER*1..3]->(g:Account {flagged: true}) RETURN shared_device, count(DISTINCT g)`, then `CREATE (s)-[:TRANSFER {..., risk}]->(d)` |
| 3. rings | cycles of 3–5 transfers ≥ 5,000 back to the start, each reported once (start = smallest id). Memgraph: `(a)-[:TRANSFER *3..5 (e, n \| e.amount >= 5000)]->(a)` (filter lambda, applied while expanding). Neo4j: `(a)-[r:TRANSFER*3..5]->(a) WHERE all(x IN r WHERE x.amount >= 5000)` |
| 3. shared devices | devices with ≥ 5 accounts, at least one flagged |
| 3. exposure | per flagged account, distinct accounts that reach it in 1–3 transfers (`<-[:TRANSFER*1..3]-`) |
| 3. top receivers | top 20 by received amount |
| 4. checks | every planted ring found; `TRANSFER` count = 320,186 and the amount sum; per analytics query a SHA-256 of the result rows, compared between the engines |

Each analytics query runs `RUNS` times (p50/p99 over the runs); the risk scores written in the
stream depend on the interleaving of the workers, so they are not compared.

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24.4 GB, Docker 29.5.3), native arm64
images, each engine capped at 4 CPUs / 6 GB, app 3 CPUs, defaults above (50k accounts, 425,079
load rows, 20,000 streamed transfers, 8 workers, 5 runs per analytics query), one run, Docker used
by this example only.

| phase | Memgraph 3.13.1 | Neo4j 2026.09.0 Community | Memgraph / Neo4j |
|---|---|---|---|
| 1. load, 425,079 rows | 2.1 s, 203,642 rows/s; batch p50 49.42 ms, p99 74.94 ms | 6.2 s, 68,855 rows/s; batch p50 129.58 ms, p99 367.87 ms | 2.96x rows/s |
| 2. stream, 20,000 transfers | 1.9 s, 10,325 transfers/s | 8.0 s, 2,503 transfers/s | 4.12x transfers/s |
| risk check (3-hop + shared device) | p50 0.20 ms, p99 1.84 ms | p50 0.56 ms, p99 25.31 ms | p50 2.8x lower, p99 13.8x lower |
| insert | p50 0.11 ms, p99 1.93 ms | p50 1.14 ms, p99 39.95 ms | p50 10.4x lower, p99 20.7x lower |
| 3. rings (50 rows) | p50 81.69 ms, p99 82.28 ms | p50 160.43 ms, p99 163.07 ms | 1.96x lower |
| 3. shared devices (63 rows) | p50 17.95 ms, p99 18.42 ms | p50 26.02 ms, p99 28.52 ms | 1.45x lower |
| 3. exposure (199 rows) | p50 16.34 ms, p99 16.44 ms | p50 36.75 ms, p99 39.35 ms | 2.25x lower |
| 3. top receivers (20 rows) | p50 241.88 ms, p99 247.58 ms | p50 207.35 ms, p99 216.67 ms | 1.17x higher (Neo4j faster) |
| memory after the run | RSS 250.75 MiB, peak 397.43 MiB (`SHOW STORAGE INFO`) | container 2.54 GiB (2 GiB heap preallocated, 1 GiB page cache) | — |

| check | Memgraph | Neo4j |
|---|---|---|
| planted rings found | 50 of 50 (10 closed by the stream) | 50 of 50 |
| `TRANSFER` count / amount sum | 320,186 / 348,414,000 | 320,186 / 348,414,000 |
| transfers with risk > 0 in the stream | 13,712 of 20,000 | 13,712 of 20,000 |
| result-row hashes (rings, shared devices, exposure, top receivers) | `719dd8f8dde8`, `14f8d33c27bb`, `6df785dc9d4a`, `5501205df00b` | identical |

- The stream phase lasts 1.9 s and 8.0 s: short single runs; repeat with a larger `STREAM` for
  steadier numbers.
- A smoke run (`ACCOUNTS=5000 DEVICES=2000 TRANSFERS=30000 STREAM=2000 RUNS=1`) also matched on
  all hashes; its Neo4j stream phase (195 transfers/s, p50 15.72 ms) was dominated by first-query
  planning and JIT warm-up, which 20,000 transfers amortise.

## Design notes

- **Memory-resident storage.** Memgraph keeps vertices, edges and properties in RAM and
  traverses pointer-linked adjacency; Neo4j reads records through its page cache (here 1 GiB, the
  graph fits). The 3-hop risk check: p50 0.20 ms vs 0.56 ms.
- **Commit durability differs by default.** Memgraph's WAL is fsynced every 100,000 transactions
  (`--storage-wal-file-flush-every-n-tx`); Neo4j flushes its transaction log on every commit. That
  is most of the insert gap (p50 0.11 ms vs 1.14 ms): the two are not equally durable per commit.
- **Expansion filters.** The ring query filters edges while expanding: a filter lambda in
  Memgraph, an `all()` predicate over the relationship list in Neo4j (its plan was not inspected).
  Both return the same 50 rings.
- **Whole-graph aggregation** (`top receivers`: every `TRANSFER`, grouped by receiver) is the one
  query where Neo4j was faster (207.35 ms vs 241.88 ms).
- **Memory footprint.** Memgraph peaked at 397.43 MiB for the graph; Neo4j's JVM held a 2 GiB
  heap plus 1 GiB page cache by configuration.
- **Benchmark claims.** Memgraph's own comparison (mgBench, [blog](https://memgraph.com/blog/memgraph-vs-neo4j-performance-benchmark-comparison))
  is vendor-run; its method (p99 of short runs, different iteration counts per engine) was
  disputed, e.g. [this critique](https://maxdemarzi.com/2023/01/11/bullshit-graph-database-performance-benchmarks/)
  by a former Neo4j employee. The numbers here come from one run of this program on one laptop.

## Known issues

- **Query syntax differs only in two places:** DDL (`CREATE INDEX ON :Account(id)` /
  `CREATE CONSTRAINT ON (a:Account) ASSERT a.id IS UNIQUE` vs `CREATE CONSTRAINT ... FOR ...
  REQUIRE`), and the ring filter (Memgraph filter lambda vs `all()` over the relationship list).
- Memgraph refuses DDL inside explicit transactions; the app runs every query as an auto-commit
  `session.Run` on both engines.
- **Disk full kills Memgraph** (`Assertion failed ... 'written > 0' ... No space left on device`,
  exit 133): the first full run on 2026-10-04 died that way (`ConnectivityError: EOF` in the app)
  when the shared Docker VM's disk filled up.

## Links

- Memgraph: https://memgraph.com/docs
- Deep path traversal and filter lambdas: https://memgraph.com/docs/advanced-algorithms/deep-path-traversal
- neo4j-go-driver: https://github.com/neo4j/neo4j-go-driver
- Memgraph's benchmark (mgBench, vendor-run): https://memgraph.com/blog/memgraph-vs-neo4j-performance-benchmark-comparison
- A critique of it: https://maxdemarzi.com/2023/01/11/bullshit-graph-database-performance-benchmarks/
