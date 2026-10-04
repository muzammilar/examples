# Memgraph

Website: https://memgraph.com/
GitHub: https://github.com/memgraph/memgraph

In-memory graph database (C++), Cypher over Bolt (Neo4j drivers work), with the MAGE algorithm
library. Images used: `memgraph/memgraph:3.13.1`, `memgraph/memgraph-mage:3.13.1`,
`memgraph/lab:3.13.2` (all multi-arch, native on arm64).

## License

- **Community** (everything run here): Memgraph BSL 1.1 with an Additional Use Grant
  ([`licenses/BSL.txt`](https://github.com/memgraph/memgraph/blob/master/licenses/BSL.txt)).
  Production use is allowed for internal business purposes, but not as a hosted
  database-as-a-service, not embedded in a product for third parties, and not in a competing
  product. Each version converts to Apache 2.0 four years after release (the file's
  `CHANGE DATE` line reads `2030-14-09`, not a valid date). Not an OSI open-source license.
- **Enterprise** (MEL, [`licenses/MEL.pdf`](https://github.com/memgraph/memgraph/blob/master/licenses/MEL.pdf)):
  high availability with automatic failover (coordinators), multi-tenancy, RBAC/LBAC, SSO,
  audit log, Prometheus metrics endpoint, TTL, parallel execution, dynamic (`*_online`) MAGE
  algorithms, CRON snapshots
  ([list](https://memgraph.com/docs/database-management/enabling-memgraph-enterprise)). The trial
  needs a form with an organization name ([enterprise-trial](https://memgraph.com/enterprise-trial));
  there is no signup-free key, so nothing Enterprise is run here.

## Examples

| folder | what |
|---|---|
| [`single-node/`](single-node) | Memgraph Community (MAGE image) + Memgraph Lab on Docker Compose: indexes and constraints, `*BFS` / `*WSHORTEST` paths, PageRank, Louvain, betweenness, snapshot + WAL recovery after SIGKILL, the social-graph benchmark from `neo4j/single-node`. |

## Clustering and scaling

- **No sharding.** Every instance holds the whole graph in RAM; the dataset must fit on one machine
  ([replication docs](https://memgraph.com/docs/clustering/replication)). Scaling out means
  read replicas.
- **Replication** (one MAIN, SYNC/ASYNC/STRICT_SYNC replicas, manual promotion) is Community.
- **Automatic failover** (Raft coordinators) is Enterprise. Without a license a coordinator
  answers `Access to high availability requires an enterprise, ai_platform, or oem license.`
  (checked on 3.13.1, 2026-10-04).

| [`docker-compose-cluster/`](docker-compose-cluster) | 3 instances (MAIN + SYNC + ASYNC replica), manual failover, read replicas 3 → 5 → 3 instances. Details: `make failover` kills the MAIN under write load and fails over by hand (what was and was not replicated, the old MAIN as a second MAIN, rejoin after a wipe); `make scale-out` / `scale-in` add and remove read replicas (3 → 5 → 3 instances) under read load; `make enterprise-ha-check` shows the coordinator refusing without a license. |

## Clustering and scaling

- **No sharding.** Every instance holds the whole graph in RAM; the dataset must fit on one
  machine. Scaling out means read replicas; writes go to the one MAIN
  ([replication docs](https://memgraph.com/docs/clustering/replication)).
- **Replication** (one MAIN, `SYNC` / `ASYNC` / `STRICT_SYNC` replicas, manual promotion with
  `SET REPLICATION ROLE TO MAIN`) is Community.
- **Automatic failover and Bolt routing** need the Raft coordinators of Enterprise HA. Without a
  license a coordinator answers `Access to high availability requires an enterprise, ai_platform,
  or oem license.` (3.13.1, 2026-10-04). The example routes reads in the client.

## Benchmark

| example | date | setup | result |
|---|---|---|---|
| [single-node](single-node/README.md#benchmark) | 2026-10-04 | social graph 100k persons / 952k edges (same harness as `neo4j/single-node`), 4 CPUs / 6 GB, Python driver, 1 run | load 79,227 persons/s and 141,226 edges/s (Neo4j 56,875 / 78,249, 1.39x / 1.80x); lookup 12,724 QPS with 8 clients, p99 1.56 ms (Neo4j 7,398 QPS, 1.72x); shortest path p50 0.29 ms (Neo4j 0.70 ms, 2.4x lower); top-10 in-degree p50 32.15 ms (Neo4j 107 ms, 3.3x lower) |
| [docker-compose-cluster](docker-compose-cluster/README.md#results) | 2026-10-04 | 3 instances, 1 CPU each, Go client, 4 writers | MAIN killed under ~450 writes/s: manual promotion 2 s after the kill, longest write gap 1.9 s, 0 acknowledged writes lost on the SYNC replica, 7 missing on the ASYNC replica at the kill (caught up later), 1 duplicate from a retried in-flight write |
| [docker-compose-cluster](docker-compose-cluster/README.md#read-replicas-make-scale-demo) | 2026-10-04 | 16 readers, 2-hop counts, 1 CPU per replica | 2 → 4 → 2 replicas: 5,048 → 11,934 → 5,710 reads/s (2.36x at 4 replicas), 0 failed reads; `REGISTER`/`DROP REPLICA` needed up to 77 retries under load |

## Known issues

Seen with Memgraph 3.13.1, 2026-10-04.

- **mgconsole and comments:** a `;` or `'` inside a `//` comment, or a comment after a statement on
  the same line, breaks a script; a comment directly before `EXPLAIN`/`PROFILE` fails with
  `missing DATABASE at 'EXPLAIN'`.
- **Shortest-path plan depends on query shape:** `MATCH (a {..}), (b {..}) MATCH p=(a)-[*BFS]->(b)`
  runs single-source BFS plus a filter (p50 77.77 ms on the benchmark graph); adding `WITH a, b`
  gives `STShortestPath` (bidirectional, p50 0.29 ms, 268x lower).
- **DDL and `SHOW VERSION` are refused inside explicit transactions** (`... is not allowed in
  multicommand transactions`), which is what Neo4j drivers' `execute_query` / managed
  transactions use. Run them as auto-commit `session.run`.
- **Telemetry is on by default** (`--telemetry-enabled=true` in the image); the examples turn it off.
- **WAL fsync every 100,000 transactions** by default: a host crash can lose acknowledged writes.
- **Manual failover only (Community).** Promotion, re-registering replicas and fencing the old MAIN
  are up to you. A restarted old MAIN comes back as MAIN and accepts writes; once it has diverged
  it cannot be registered as a replica and has to be wiped.
- **`REGISTER REPLICA` / `DROP REPLICA` fail intermittently under load** (`Error: 6` = `NO_ACCESS`;
  `lack of unique access over the cluster state`); retry them.
- **ASYNC replica status flips** between `ready`, `replicating`, `recovery` and `invalid` under
  writes; `behind` goes negative.
- **Disk full kills the process** (`Assertion failed ... 'written > 0' ... No space left on device`, exit 133).
- **`vm.max_map_count`**: Memgraph wants at least 524288 (Docker Desktop's VM has 262144).
