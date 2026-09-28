# NebulaGraph

Website: https://www.nebula-graph.io/

- [`single-node/`](single-node) — one metad, storaged and graphd with Docker Compose: spaces, tags/edges, GO traversals, LOOKUP, MATCH and FIND SHORTEST PATH in nGQL.

## Benchmark

Social-graph benchmark (100k persons, 952k `follows` edges) on 4 CPUs / 6 GB across metad/graphd/storaged (Apple M4 Pro, Docker VM aarch64, 2026-09-28): edges load at 173k/s, but indexed vertex inserts manage only 9.5k/s; lookup by handle 1,790 QPS with 1 client (p99 0.68 ms); 1-hop 0.51 ms, 2-hop 0.93 ms, shortest path 2.5 ms. Local adjacency is quick, but a whole-graph aggregation (top-10 most-followed) takes 3.5 s. Full table and method: [`single-node/README.md`](single-node/README.md#benchmark).
