# Weaviate — 3-node cluster with Docker Compose

Three Weaviate nodes with no vectorizer module (`vectorizer: none`, the client supplies every
vector). `make test` and `make failover` drive REST and GraphQL with curl + jq from a small
`tools` container.

```bash
make up       # start the three nodes and wait for /v1/.well-known/ready on each
make test     # run scripts/test.sh: 3 healthy nodes, 3 shards x 3 replicas, QUORUM insert, placement, reads at ONE/QUORUM/ALL
make failover # stop weaviate-3 (QUORUM works), restart it; partition it (ALL fails, QUORUM works), heal it
make status   # node status, collections with shards/replicas, and the Landmark object count
make cli      # shell with curl + jq on the compose network (APIs at http://weaviate-{1,2,3}:8080)
make down     # remove containers, volumes and the locally built tools image
```

- REST: http://localhost:18080/v1 (weaviate-1), http://localhost:18081/v1 (weaviate-2),
  http://localhost:18082/v1 (weaviate-3), e.g. `curl localhost:18080/v1/nodes`
- GraphQL: `POST http://localhost:18080/v1/graphql`
- gRPC: `localhost:50151` (weaviate-1)

The host ports are offset from [`../single-node`](../single-node) (8080/50051), so both can run
at once. Anonymous access is enabled, no API key; ports are bound to localhost because there
is no auth. Usage telemetry is off (`DISABLE_TELEMETRY`). Any node answers any request and
forwards it to the replicas it needs.

## Cluster configuration

| variable | value | purpose |
| --- | --- | --- |
| `CLUSTER_HOSTNAME` | `node1`, `node2`, `node3` | node name in memberlist, Raft and `/v1/nodes` |
| `CLUSTER_GOSSIP_BIND_PORT` | `7100` | memberlist gossip (membership, node addresses) |
| `CLUSTER_DATA_BIND_PORT` | `7101` | internal HTTP API for replica reads/writes; must be gossip + 1 |
| `CLUSTER_JOIN` | `weaviate-1:7100` (node2, node3) | gossip address of a node to join |
| `RAFT_JOIN` | `node1,node2,node3` | Raft voters, by `CLUSTER_HOSTNAME` |
| `RAFT_BOOTSTRAP_EXPECT` | `3` | voters to wait for before the first leader election |

Raft holds the schema and the shard-to-node placement. Objects are replicated by the
coordinating node, not by Raft. Because the first node waits for all three Raft voters before it
becomes ready, the nodes start together instead of one after another. Each node uses the same
ports in its own container; the [upstream example](https://docs.weaviate.io/deploy/installation-guides/docker-installation)
gives each node different ports only because it could also run on one host.

## Sharding and replication

[`requests/01-create-collection.json`](requests/01-create-collection.json) creates `Landmark`
with `shardingConfig.desiredCount: 3` and `replicationConfig.factor: 3`: three shards, each
stored on all three nodes. `make test` inserts six objects with `?consistency_level=QUORUM`
(2 of 3 replicas must acknowledge), prints the placement from `/v1/nodes/Landmark?output=verbose`
and reads an object by id at `ONE`, `QUORUM` and `ALL` from each node, then runs a `nearVector`
query at each level:

```
{"shard":"sTnpljgl6ksA","replicas":["node1","node2","node3"]}
{"shard":"ulJc3Wt5QnKv","replicas":["node1","node2","node3"]}
{"shard":"wJCvFrElUreB","replicas":["node1","node2","node3"]}
```

Async replication (`replicationConfig.asyncEnabled`) is on by default in 1.39: replicas compare
hash trees in the background and copy what they miss. `make test` drops and recreates
`Landmark` each run.

## Failover

`make failover` runs [`scripts/failover.sh`](scripts/failover.sh) in four phases:

1. **Stop `weaviate-3`.** A QUORUM batch insert of two objects and QUORUM reads by id and by
   vector still succeed with 2 of 3 replicas.
2. **Start it again.** The script waits (at most 120 s) until `/v1/nodes` shows three `HEALTHY`
   nodes, reads at `ALL`, and waits until the Aggregate count on `weaviate-3` includes the two
   objects it missed (async replication).
3. **Partition `weaviate-3`.** `iptables` in its network namespace (the `tools` image with
   `NET_ADMIN`) rejects connections to its data port 7101. The node stays in memberlist and
   Raft but cannot serve replica requests, so `/v1/nodes` shows it `UNAVAILABLE`. A read by id
   at `ALL` fails (`cannot achieve consistency level "ALL"`), a batch insert at `ALL` fails
   (`cannot reach enough replicas: required 3 replicas, got 2`), and QUORUM reads and writes
   succeed. The script asserts each result.
4. **Heal it.** The rule is flushed; the script waits for three healthy nodes, reads at `ALL`,
   and checks that the rejected `ALL` write was not applied (404).

Why partition instead of asserting `ALL` in phase 1: a node that shuts down leaves memberlist at
once, and since 1.31 Weaviate computes the consistency level over the replicas memberlist still
knows, not over the shard's replica set. With `weaviate-3` stopped, `ALL` needs only the two
remaining replicas and succeeds; phase 1 prints that read without asserting it. This is
[weaviate/weaviate#13302](https://github.com/weaviate/weaviate/issues/13302) (open; fix in
[#13303](https://github.com/weaviate/weaviate/pull/13303)). A killed node gives the same result
once memberlist declares it dead (within about 30 s here). The same issue notes that searches
(`nearVector`, `bm25`, ...) do not enforce the consistency level: an `ALL` search returns results
with a replica down, so `make failover` asserts `ALL` only on reads by id and on writes.
