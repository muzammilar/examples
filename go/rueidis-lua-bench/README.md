# rueidis-lua-bench

A small Go benchmark for Redis-compatible servers. It runs SET, GET and three Lua scripts (`lua/add.lua`, `lua/update.lua`, `lua/delete.lua`, which add, update with a version check, and delete a hash) through [rueidis](https://github.com/redis/rueidis), and prints ops/s, p50 and p99 for each. It works on a single node, a primary or a cluster; rueidis detects cluster mode on connect and routes each key to its primary.

```sh
go run . -addr 127.0.0.1:6379 -n 200000 -c 50 -keys 100000
```

`-addr` is the server (any node of a cluster), `-n` the operations per command, `-c` the concurrent workers and `-keys` the number of distinct keys. The `Dockerfile` builds a static image of it.

The Valkey and Dragonfly examples link it as `bench` (`bench -> ../../go/rueidis-lua-bench`) and run it from their Makefiles with `make bench-lua`: [valkey/minimal-lua](../../valkey/minimal-lua), [valkey/helm-chart](../../valkey/helm-chart), [valkey/valkey-operator](../../valkey/valkey-operator), [valkey/ot-redis-operator](../../valkey/ot-redis-operator), [dragonfly/single-node](../../dragonfly/single-node), [dragonfly/docker-compose-cluster](../../dragonfly/docker-compose-cluster), [dragonfly/helm-chart](../../dragonfly/helm-chart) and [dragonfly/dragonfly-operator](../../dragonfly/dragonfly-operator).

## Results summary

Valkey 9.1.2 against Dragonfly v2.0.0, `-n 1000000 -c 50 -keys 100000`, on an Apple M4 Pro (Docker Desktop VM), one setup at a time, 2026-10-03. Valkey was ahead in every setup, most of all on the Lua scripts: Dragonfly doesn't fly with Lua here.

- Single node, 2 CPUs each: Valkey runs the three scripts 2.7-3x faster (`add.lua` 490k vs 170k ops/s); plain SET/GET is 1.6x faster (539k vs 337k).
- Cluster, 3 primaries + 3 replicas at 1 CPU per node: Valkey is 1.5-2.5x faster on the scripts (`update.lua` 316k vs 125k ops/s), while GET is about even.
- kind, primary + replicas: Valkey's primary runs `add.lua` at 364k ops/s against 149k for the Dragonfly Helm chart and 253k for the Dragonfly operator; pod resources differ, so treat it as rough.

Full tables, with the `valkey-benchmark` RESP numbers next to these, are in each example's README.
