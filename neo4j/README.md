# Neo4j

Website: https://neo4j.com/

- [`single-node/`](single-node) — Neo4j Community with Docker Compose: constraints, MERGE, variable-length paths, shortestPath and aggregation in Cypher.
- [`docker-compose-cluster/`](docker-compose-cluster) — three-primary Neo4j cluster with Docker Compose (Enterprise Edition, evaluation license — you must accept it via `NEO4J_ACCEPT_LICENSE_AGREEMENT`; `make up` refuses otherwise): `SHOW SERVERS`/`SHOW DATABASES`, `neo4j://` routing to the leader, `CREATE DATABASE ... TOPOLOGY 3 PRIMARIES`, and a leader failover demo.

## Benchmark

Social-graph benchmark (100k persons, 952k `follows` edges) on 4 CPUs / 6 GB (Apple M4 Pro, Docker VM aarch64, 2026-09-28): load 57k persons/s and 78k edges/s; lookup by handle 1,959 QPS with 1 client and 7,398 QPS with 8 (p99 4.4 ms); shortest path p50 0.70 ms; top-10 most-followed over all edges 107 ms. It handles concurrent reads and path queries well, and bulk load is its slowest part. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
