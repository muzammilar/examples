# Dragonfly

Website: https://www.dragonflydb.io/ · GitHub: https://github.com/dragonflydb/dragonfly

| Folder | What |
|---|---|
| [`single-node/`](single-node) | One Dragonfly on Docker Compose; Go client ([rueidis](https://github.com/redis/rueidis)) calling Lua scripts for add, update, delete, plus `make bench`. Same code as [`../valkey/minimal-lua`](../valkey/minimal-lua), for comparison. |
| [`docker-compose-cluster/`](docker-compose-cluster) | Native cluster mode: 3 shards x (primary + replica), slot layout pushed with `DFLYCLUSTER CONFIG`, slot migration, manual failover |
| [`dragonfly-operator/`](dragonfly-operator) | Primary + replicas `Dragonfly` on kind, official Dragonfly Operator; failover, snapshots, scaling |
| [`helm-chart/`](helm-chart) | Official Helm chart on kind as primary + 2 replicas |
| [`features/`](features) | One node: memcached protocol, JSON, search with vectors, Bloom filters, emulated cluster mode, snapshots |

Each folder has a `Makefile` (`make up`, `make test`, `make down`, ...) that creates everything it needs. Tools come from the repo-root `flake.nix` (`direnv allow` or `nix develop` at the root).

## Benchmark summary

`BENCH_N=1000000`, 50 clients, 100k keys, Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03. ops/s; full tables and Valkey comparisons in each folder.

| Setup | SET | GET | SET `-P 16` | add.lua | update.lua |
|---|---:|---:|---:|---:|---:|
| [`single-node`](single-node) (2 threads, 2 CPUs) | 231,160 | 237,925 | 2,380,952 | 169,862 | 150,092 |
| [`docker-compose-cluster`](docker-compose-cluster) (1 CPU/node) | 173,461 | 265,745 | 360,881 | 283,854 | 125,438 |
| [`dragonfly-operator`](dragonfly-operator) (master) | 220,264 | 334,001 | 395,101 | 253,241 | 122,219 |
| [`helm-chart`](helm-chart) (`dragonfly-0`) | 273,075 | 256,016 | 755,287 | 148,641 | 86,837 |

SET/GET columns are `valkey-benchmark`; Lua columns are `bench-lua` (rueidis).

## Known issues

- **No control plane for cluster mode in open-source Dragonfly:** something outside it has to assign slots and promote replicas.
- **The operator does not shard:** it handles failover for a primary + replicas only.
