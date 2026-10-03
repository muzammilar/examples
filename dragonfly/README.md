# Dragonfly

Website: https://www.dragonflydb.io/

- [`single-node/`](single-node) — one Dragonfly on Docker Compose and a Go client ([rueidis](https://github.com/redis/rueidis)) calling Lua scripts for add, update and delete, plus `make bench`. Same code as [`../valkey/minimal-lua`](../valkey/minimal-lua), for comparing the two.
- [`docker-compose-cluster/`](docker-compose-cluster) — native cluster mode: 3 shards x (primary + replica), slot layout pushed with `DFLYCLUSTER CONFIG`, slot migration and a manual failover.
- [`dragonfly-operator/`](dragonfly-operator) — a primary + replicas `Dragonfly` on kind managed by the official Dragonfly Operator, with failover, snapshots and scaling.
- [`helm-chart/`](helm-chart) — the official Helm chart on kind, as a primary + 2 replicas.
- [`features/`](features) — one node showing what Dragonfly has built in: memcached protocol, JSON, search with vectors, Bloom filters, emulated cluster mode, snapshots.

Each directory has a `Makefile` (`make up`, `make test`, `make down`, ...) that creates everything it needs.
Tools come from the repo-root `flake.nix` (`direnv allow` or `nix develop` at the root).

Open-source Dragonfly has no control plane for cluster mode: something outside it has to assign slots and
promote replicas. The operator handles failover for a primary + replicas, but does not shard.
