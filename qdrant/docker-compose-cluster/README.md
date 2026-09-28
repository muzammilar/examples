# Qdrant — 3-node cluster with Docker Compose

Three Qdrant peers in distributed mode (`QDRANT__CLUSTER__ENABLED=true`): peers agree on
cluster and collection metadata over Raft on the internal p2p port `6335`. `qdrant-1` starts the
cluster (`--uri http://qdrant-1:6335`); `qdrant-2` and `qdrant-3` join it with
`--bootstrap http://qdrant-1:6335`, one after another once the previous peer is ready. Each peer
also passes its own `--uri`, so the cluster stores hostnames rather than container IPs that can
change on a restart. `make test` and `make failover` drive the REST API with curl + jq from a
small `tools` container.

```bash
make up       # start the three peers and wait for /readyz on each
make test     # run scripts/test.sh: /cluster, 3-shard x 2-replica collection, upsert, placement, count, search
make failover # stop qdrant-3: search + upsert still work, its replicas go Dead; restart it, wait for Active
make status   # Raft role, term and commit index per peer, collections
make cli      # shell with curl + jq on the compose network (APIs at http://qdrant-{1,2,3}:6333)
make down     # remove containers, volumes and the locally built tools image
```

- REST: http://localhost:16333 (qdrant-1), http://localhost:16343 (qdrant-2),
  http://localhost:16353 (qdrant-3), e.g. `curl localhost:16333/cluster`
- Web UI: http://localhost:16333/dashboard
- gRPC: `localhost:16334` (qdrant-1)

The host ports are offset from [`../single-node`](../single-node) (6333/6334), so both can run
at once. They are bound to localhost because there is no auth (no API key is set). Anonymous
telemetry is off (`QDRANT__TELEMETRY_DISABLED`). Any peer answers any request: it forwards
reads and writes to the peers holding the shards it does not have.

## Sharding and replication

[`requests/01-create-collection.json`](requests/01-create-collection.json) creates `demo` with
`shard_number: 3`, `replication_factor: 2`, `write_consistency_factor: 1`: three shards, each
stored on two of the three peers, and an update succeeds once one replica of each touched shard
has applied it (Qdrant still sends it to all replicas). `make test` prints the placement from
`GET /collections/demo/cluster` (`local_shards` plus `remote_shards`), e.g.:

```
{"shard":0,"replicas":["qdrant-1: Active","qdrant-3: Active"]}
{"shard":1,"replicas":["qdrant-3: Active","qdrant-2: Active"]}
{"shard":2,"replicas":["qdrant-1: Active","qdrant-2: Active"]}
```

With a replication factor of 2 every shard survives the loss of any one peer. `make test`
drops and recreates `demo` each run.

## Failover

`make failover` stops `qdrant-3` and runs [`scripts/failover.sh`](scripts/failover.sh) `down`:

- `GET /cluster` on qdrant-1 still lists three peers, but counts Raft `message_send_failures`
  to `qdrant-3`; qdrant-1 and qdrant-2 are a majority, so there is still a leader and metadata
  changes still commit.
- Search and an exact count return every point from the remaining replica of each shard.
- An upsert of six more points succeeds (`write_consistency_factor: 1`). The replicas on
  `qdrant-3` missed it, so the cluster marks them `Dead` and stops routing to them.

Then it starts `qdrant-3` again and runs `failover.sh up`: the peer rejoins, its `Dead` replicas
are recovered by a shard transfer from the live replica, and the script polls (2 s steps, at
most 120 s) until every replica is `Active` and all three peers count 12 points. `make test`
runs cleanly afterwards.

With `write_consistency_factor: 2` the upsert above would fail for shards with a replica on the
stopped peer. With two of the three peers down, Raft has no majority and metadata changes
(creating or deleting collections, marking replicas Dead) cannot commit.
