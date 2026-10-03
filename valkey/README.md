# Valkey

Website: https://valkey.io/ · GitHub: https://github.com/valkey-io/valkey

| Folder | What |
|---|---|
| [`helm-chart/`](helm-chart) | Official `valkey/valkey` Helm chart on kind as primary + 2 replicas, plus a 6-node cluster (3 primaries + 3 replicas) on Docker Compose |
| [`valkey-operator/`](valkey-operator) | 3-shard x 1-replica `ValkeyCluster` with persistence on kind, official Valkey Operator; scale by shard |
| [`ot-redis-operator/`](ot-redis-operator) | 3-leader + 3-follower `RedisCluster` on kind, OT-CONTAINER-KIT redis-operator running upstream `valkey/valkey`; scale by leader |
| [`minimal-lua/`](minimal-lua) | One Valkey on Docker Compose; Go client ([rueidis](https://github.com/redis/rueidis)) calling Lua scripts for add, update, delete |

Each folder has a `Makefile` (`make up`, `make test`, `make down`, ...) that creates everything it needs, including the kind cluster, with defaults at the top. Tools come from the repo-root `flake.nix` (`direnv allow` or `nix develop` at the root).

## Benchmark summary

`BENCH_N=1000000`, 50 clients, 100k keys, Apple M4 Pro (Docker Desktop VM, 11 CPUs), 2026-10-03. ops/s; full tables in each folder.

| Setup | SET | GET | SET `-P 16` | add.lua | update.lua |
|---|---:|---:|---:|---:|---:|
| [`minimal-lua`](minimal-lua) (1 node, 2 CPUs) | 268,240 | 269,978 | 3,401,360 | 489,724 | 406,756 |
| [`helm-chart`](helm-chart) kind (primary + 2 replicas) | 270,490 | 320,924 | 1,366,120 | 363,872 | 220,430 |
| [`helm-chart`](helm-chart) Compose cluster (1 CPU/node) | 444,247 | 499,750 | 1,996,008 | 446,089 | 315,508 |
| [`valkey-operator`](valkey-operator) (3 shards) | 234,577 | 331,565 | 1,331,558 | 268,417 | 177,434 |
| [`ot-redis-operator`](ot-redis-operator) (3 leaders) | 265,111 | 398,248 | 1,331,558 | 331,399 | 166,841 |

SET/GET columns are `valkey-benchmark`; Lua columns are `bench-lua` (rueidis).

## Known issues

- **Official chart has no cluster mode:** standalone or primary/replica only, no Sentinel. The cluster in `helm-chart/` therefore runs on Docker Compose; the kind part is primary/replica.
- **Official operator is early:** its README says not ready for production; API `v1alpha1`.
- **OT operator relies on an alpha flag:** no Valkey mode; it runs Valkey only with `featureGates.GenerateConfigInInitContainer=true`.
