# Neo4j — 3-primary cluster with Docker Compose

Three Neo4j Enterprise Edition servers (`neo4j:2026.09.0-enterprise`, 384 MiB heap, 128 MiB page
cache, 1 GiB container limit each) forming one cluster: every database is hosted as three Raft
primaries, one of which is the leader (the writer).

## License

**This example runs Neo4j Enterprise Edition, which is not open source: you must accept Neo4j's
license before it starts.** The example does not accept it for you; you pass your choice in
`NEO4J_ACCEPT_LICENSE_AGREEMENT`, which Docker Compose hands through to the containers:

- `eval` — you accept the [Neo4j Enterprise evaluation agreement](https://neo4j.com/terms/enterprise_us/)
  (evaluation use only)
- `yes` — you hold a commercial Neo4j license ([licensing terms](https://neo4j.com/terms/licensing/),
  [licensing overview](https://neo4j.com/licensing/))

`make up` refuses to start unless you export one of the two values yourself:

```bash
export NEO4J_ACCEPT_LICENSE_AGREEMENT=eval   # or =yes with a commercial license
```

The value is never written into any file here. Clustering, `CREATE DATABASE` and the
`TOPOLOGY` clause are Enterprise features; the Community image
([`../single-node`](../single-node)) has none of them.

## Usage

```bash
make up       # refuse without the license variable; start, wait for 3 servers + all databases online
make test     # SHOW SERVERS / SHOW DATABASE, cypher/*.cypher via neo4j:// routing, CREATE DATABASE demo2
make failover # stop the leader of `neo4j`: new leader, routed writes still work; start it, wait until healthy
make status   # SHOW SERVERS + SHOW DATABASES (role, writer, status per server)
make cli      # interactive cypher-shell over a routing connection (neo4j://neo4j-1:7687)
make down     # remove containers and volumes
```

- Bolt (neo4j-1): `127.0.0.1:17687`, HTTP/Browser: http://127.0.0.1:17474 — offset from
  [`../single-node`](../single-node) (7687/7474) so both can run, bound to localhost only
- Credentials: `neo4j` / `demo-password` (fixed demo value, set via `NEO4J_AUTH`)
- Discovery: a static list, `dbms.cluster.endpoints=neo4j-1:6000,neo4j-2:6000,neo4j-3:6000`
  (the v2 discovery service, the only one in 2025.x/2026.x); each server advertises its
  container name (`server.default_advertised_address`) for Bolt (7687), cluster (6000),
  Raft (7000) and server-side routing (7688)
- `initial.dbms.default_primaries_count=3`: the default `neo4j` database (and new databases
  without a `TOPOLOGY`) get three primaries

The logic and assertions live in [`cluster.sh`](cluster.sh); a failed check prints `FAIL:` and exits
non-zero. `make test` checks:

1. `SHOW SERVERS`: three servers `Enabled` / `Available`
2. `SHOW DATABASE neo4j`: three `online` primaries, exactly one `writer` (the Raft leader; the others
   are followers), and the `WRITE` entry of the routing table (`dbms.routing.getRoutingTable`) is
   that leader
3. [`cypher/01-graph.cypher`](cypher/01-graph.cypher) through `neo4j://` (cypher-shell's routing
   driver sends the write transaction to the leader), then
   [`cypher/02-read.cypher`](cypher/02-read.cypher) as read transactions (routed to a follower); the
   person count and the `KNOWS` path are asserted
4. [`cypher/03-demo2.cypher`](cypher/03-demo2.cypher): `CREATE DATABASE demo2 IF NOT EXISTS
   TOPOLOGY 3 PRIMARIES`, then `SHOW DATABASE demo2` until three primaries are online with one
   writer, and a routed write into it

Everything is idempotent (`MERGE`, `IF NOT EXISTS`), so `make test` also runs after `make failover`.

`make failover` asks for the leader of `neo4j` (`SHOW DATABASE neo4j YIELD address, writer`),
stops that container, waits (max 60 s) for a different writer, checks that only two servers are
available, writes a `:Failover` node and a `demo2` update through `neo4j://` on a surviving server,
starts the stopped one, waits (max 180 s) for three healthy containers, three available servers and
every database online on all three, and reads the `:Failover` count directly on the restarted server
(`bolt://`, read mode) to show it caught up from the Raft log.

Caveats:

- `docker compose stop` shuts Neo4j down cleanly (the leader hands over leadership), so the new
  leader is usually there by the time the stop returns; `stop_grace_period` is 60 s because the
  default 10 s ends in a SIGKILL. A crash (`docker kill`) waits for the Raft election timeout instead.
- Three primaries tolerate one failure; with two down, databases lose write quorum.
- The routing table advertises container names, so `neo4j://` routing only works inside the compose
  network (`make test` runs cypher-shell in a container). From the host, use `bolt://127.0.0.1:17687`
  (a direct connection to neo4j-1) or advertise host-reachable addresses.
- Each JVM uses ~0.8–1 GiB of its 1 GiB limit at idle; raise `mem_limit` along with heap and page
  cache for real data.
