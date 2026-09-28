# ArangoDB — cluster with Docker Compose

An ArangoDB 3.12 cluster, one `arangod` per container and role:

| role | containers | what it does |
|---|---|---|
| Agent | `agent-1..3` | the agency: Raft-replicated cluster config (Plan / Current) and the supervision that detects failed servers and fails over shards |
| DB-server | `dbserver-1..3` | stores the shards; each shard has one leader and `replicationFactor - 1` synchronous followers |
| Coordinator | `coordinator-1`, `coordinator-2` | stateless HTTP API, web UI and AQL front end; plans queries and sends parts to the shards' leaders |

This is the layout the ArangoDB docs describe for a minimal cluster (3 agents for a Raft
majority, at least 2 DB-servers, 1+ coordinators); 3 DB-servers leave room to re-home a shard's
replica when one fails.

```bash
make up        # start all 8 containers, wait for the healthchecks, print cluster health
make test      # run js/0*.js: health, a 3-shard RF=2 collection + shard distribution, AQL, graph traversal
make failover  # stop a DB-server: FAILED, followers promoted, reads + writes go on; restart, GOOD + in sync
make status    # version + cluster health
make cli       # interactive arangosh on coordinator-1, database demo
make down      # remove containers and volumes
```

- HTTP API / web UI: http://127.0.0.1:8539 (coordinator-1) and http://127.0.0.1:8540 (coordinator-2),
  user `root`, password `demo` — demo only; bound to localhost and offset from
  [`../single-node`](../single-node) (8529). Agents and DB-servers are not published.

**Why plain `arangod` per role, not the Starter.** The [ArangoDB Starter](https://docs.arango.ai/arangodb/stable/components/tools/arangodb-starter/)
(`arangodb`) is the recommended way to run a cluster on hosts or VMs: one starter per machine
launches an agent, a DB-server and a coordinator there. In Docker, each starter container then runs
all three roles as child processes (or gets the Docker socket to launch sibling containers), so one
container is no longer one role. The docs'
[manual start in Docker](https://docs.arango.ai/arangodb/stable/deploy/cluster/deployment/manual-start/)
runs one `arangod` per container with `--agency.*` / `--cluster.*` flags, which maps onto Compose
services, so each role is a container you can stop and inspect. That is what
[`docker-compose.yml`](docker-compose.yml) does:

- agents: `--agency.activate true --agency.size 3 --agency.supervision true --agency.my-address ... --agency.endpoint ...` (x3)
- DB-servers / coordinators: `--cluster.my-role DBSERVER|COORDINATOR --cluster.my-address ... --cluster.agency-endpoint ...` (x3)
- all: `--server.jwt-secret-keyfile /secrets/jwt-secret` (cluster-internal auth; the demo secret is
  in [`secrets/jwt-secret`](secrets/jwt-secret)) and authentication on. `SKIP_DATABASE_INIT=1` skips the
  image's single-server init step; the root password comes from `ARANGODB_DEFAULT_ROOT_PASSWORD`,
  which the coordinators apply when they bootstrap `_system`.
- `ARANGODB_OVERRIDE_DETECTED_TOTAL_MEMORY=1G` sizes each process's caches for 1 GiB instead of
  the whole Docker VM; the 8 processes then use about 2.5 GB together.

The same `arangodb:3.12.12` image as the single node. Since 3.12.5 there is one image for all
editions; it reports `license: enterprise` and runs under the ArangoDB Community License, which
includes cluster deployments (all former Enterprise features too) with a 100 GiB dataset limit for
the whole deployment and no commercial production use (see [Features](https://docs.arango.ai/arangodb/stable/features/)).

What `make test` checks ([`js/`](js), run in arangosh on coordinator-1; each assertion fails the run):

1. `01-health.js`: `/_admin/cluster/health`: 3 agents, 3 DB-servers, 2 coordinators all `GOOD`, one agency leader.
2. `02-sharded-collection.js`: collection `demo.orders` with `numberOfShards: 3, replicationFactor: 2`,
   1,000 documents; `/_admin/cluster/shardDistribution` shows each shard's leader and follower
   (by container) and per-shard counts, every shard in sync, spread over all three DB-servers.
3. `03-aql.js`: a `COLLECT` aggregation through the coordinator (checked totals), and the plan's
   `RemoteNode` / `GatherNode` that fan out to the shards and merge.
4. `04-graph.js`: a general named graph `social` (not a SmartGraph) with 3-shard vertex and edge
   collections; a 1..2-hop `OUTBOUND` traversal and a `SHORTEST_PATH`, both with checked results.

The scripts drop and recreate what they create, so `make test` can run again (also after `make failover`).

`make failover` ([`failover/`](failover)) creates `demo.events` (3 shards, RF 2, 100 documents) and
stops the DB-server leading its first shard. Within bounded waits the agency's supervision marks it
`FAILED` in `/_admin/cluster/health` (about 10 s, the default grace period) and promotes the in-sync
follower of every shard it led; then 100 more documents are written and all 200 read back through
the coordinator. The server is started again and within 120 s the health is all `GOOD` and every
shard in `demo` is in sync (Current = Plan) with 2 replicas.

Once a DB-server is `FAILED`, the supervision also adds a new follower on the remaining DB-server
for the shards that lost a replica, so a server that comes back may hold none of the shards it had
before: it does not get them back automatically (moving shards back is a manual `moveShard` /
rebalance). New collections use it again; `make test` after `make failover` shows it holding shards.
With 2 DB-servers down a shard with both replicas there is unavailable; with 2 agents down the
agency has no majority and the cluster cannot change its configuration or fail over.
