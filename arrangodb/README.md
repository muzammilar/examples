# ArangoDB

Website: https://arangodb.com/

- [`single-node/`](single-node) — one ArangoDB server with Docker Compose: documents, edges, UPSERT, a persistent index, graph traversal and shortest path in AQL.
- [`docker-compose-cluster/`](docker-compose-cluster) — a cluster with Docker Compose (3 agents, 3 DB-servers, 2 coordinators): sharded, replicated collections, AQL and a graph traversal across shards, and DB-server failover.

## Benchmark

Social-graph benchmark (100k persons, 952k `follows` edges) on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): bulk load ~120k rows/s; lookup by handle 2,386 QPS with 1 client (p99 0.74 ms); 1-hop, 2-hop and shortest-path queries all around 0.4–1.1 ms. Edge-index traversals are cheap, and a whole-graph aggregation (top-10 most-followed) is the slow case at ~150 ms. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
