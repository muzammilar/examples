# Valkey

Website: https://valkey.io/

- [`helm-chart/`](helm-chart) — the official `valkey/valkey` Helm chart on kind as primary + 2 replicas, plus a 6-node Valkey cluster (3 primaries + 3 replicas) on Docker Compose.
- [`valkey-operator/`](valkey-operator) — a 3-shard x 1-replica `ValkeyCluster` with persistence on kind, managed by the official Valkey Operator; scale up/down by shard.
- [`ot-redis-operator/`](ot-redis-operator) — a 3-leader + 3-follower `RedisCluster` on kind managed by OT-CONTAINER-KIT redis-operator, running the upstream `valkey/valkey` image; scale up/down by leader.
- [`minimal-lua/`](minimal-lua) — one Valkey instance on Docker Compose and a Go client ([rueidis](https://github.com/redis/rueidis)) calling Lua scripts for add, update and delete.

Each directory has a `Makefile` (`make up`, `make test`, `make down`, ...) that creates everything it needs,
including the kind cluster, with its defaults at the top. Tools come from the repo-root `flake.nix` (`direnv allow` or `nix develop` at the root).

## Caveats

- **Official chart has no cluster mode:** it only does standalone or primary/replica, and has no Sentinel. That's why the cluster in `helm-chart/` runs on Docker Compose; the kind part is primary/replica.
- **The official operator is early:** its own README says it is not ready for production. Its API is `v1alpha1`.
- **The OT operator relies on an alpha flag:** it has no Valkey mode. It runs Valkey only because it's installed with `featureGates.GenerateConfigInInitContainer=true`.
