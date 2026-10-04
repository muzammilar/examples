# Manticore Search — replication cluster on Docker Compose

Three Manticore Search 29.9.0 nodes in one replication cluster (`c`; Galera-based synchronous
multi-master replication) with a replicated table and an auto-sharded table (`shards='6' rf='2'`),
a failover test under load, and scale-out / scale-in (3 → 5 → 3 nodes) under load.

## Quick start

```bash
make up         # 3 nodes, CREATE CLUSTER / JOIN CLUSTER, tables logs (replicated) and events (sharded)
make test       # cluster state; write on one node, read on another, for both tables
make failover   # Go load on all nodes; kill manticore-2, restart it; count every acknowledged row
make scale-out  # under load: start manticore-4 and -5, JOIN CLUSTER, wait for shard rebalancing (RATE=1000 rows/s)
make scale-in   # under load: stop manticore-5, then -4, wait for rf=2 to be restored each time
make status     # containers, cluster state per node, SHOW SHARDING STATUS
make cli        # mysql client on manticore-1
make down       # remove containers, volumes and the built load image
```

## Setup

| service | container | address | role |
|---|---|---|---|
| `manticore-1` | `manticore-cc-1` | 172.28.30.11, host `127.0.0.1:9326` (SQL, `MANTICORE_PORT`), `127.0.0.1:9328` (HTTP) | creates cluster `c` |
| `manticore-2`, `manticore-3` | `manticore-cc-2`, `-3` | 172.28.30.12, .13 | `JOIN CLUSTER c AT 'manticore-1:9312'` |
| `manticore-4`, `manticore-5` (profile `scale`) | `manticore-cc-4`, `-5` | 172.28.30.14, .15 | added by `make scale-out`, removed by `make scale-in` |
| `load` (profile `load`) | `manticore-cc-load` | – | [`client/`](client): Go, `go-sql-driver/mysql` over the MySQL protocol |

- Image `manticoresearch/manticore:29.9.0` (arm64 native; `MANTICORE_VERSION`), one named volume per node.
- Fixed IPs: nodes register in the cluster by IP (`cluster_c_nodes_set`), so a restarted
  container must come back with the same address.
- `searchd_listen` is reordered so `$ip:9312` comes first (see Known issues), and
  `searchd_auto_schema=0` (see Failover).
- [`scripts/bootstrap.sh`](scripts/bootstrap.sh) (run by `make up`, idempotent) creates:

| table | how | data placement |
|---|---|---|
| `logs` | `CREATE TABLE logs (...) engine='columnar'` + `ALTER CLUSTER c ADD logs` | a full copy on every node; writes go to `c:logs` on any node and are certified and applied on all nodes before the INSERT returns |
| `events` | `CREATE TABLE c:events (...) engine='columnar' shards='6' rf='2'` | 6 shards, each a local table `system.events_s<N>` on 2 nodes, kept in sync by its own internal replication cluster per node pair; `events` itself is a `type='shard'` table that fans reads and writes out to the shards (by the plain name, on any node). Buddy's sharding master places the shards and rebalances them when nodes join or fail. Auto-sharding is new in Manticore 27.1.5. |

## What `make test` shows

[`scripts/test.sh`](scripts/test.sh), output from 2026-10-04:

- All three nodes `primary / synced`, `cluster_c_size` 3, `cluster_c_nodes_set` `172.28.30.11:9312,172.28.30.12:9312,172.28.30.13:9312`.
- Writing to a cluster table without the prefix fails: `table 'logs' is a part of cluster 'c', use 'c:logs'`.
  Rows written as `c:logs` on manticore-2 are read with `HIGHLIGHT()` on manticore-3.
- `SHOW SHARDING STATUS events`: 12 rows (6 shards x 2 nodes), all `active`, `rf_status ok`:
  manticore-1 holds shards 0,1,3,5, manticore-2 1,2,3,4, manticore-3 0,2,4,5. `SHOW SHARDING MASTER`: `172.28.30.11:9312 active`.
- 6 rows inserted into `events` on manticore-3 are read back on manticore-1 and aggregated on manticore-2.
- `SHOW CREATE TABLE events OPTION force=1` shows the generated topology: one
  `agent='<node A>:9312:system.events_sN|<node B>:9312:system.events_sN[retry_count=2,ha_strategy=noerrors]'` per shard (the two copies as mirrors).

## Failover

[`scripts/failover.sh`](scripts/failover.sh): the Go load (4 writers, `REPLACE` batches of 200
rows into `c:logs` and then into `events`, round-robin over the three nodes; a failed batch is
retried on the next node until it succeeds; 4 readers running full-text + filter + `GROUP BY`
on both tables) runs for 60 s. At 15 s `docker kill manticore-cc-2` (SIGKILL), at ~42 s
`docker start`. At the end the client counts this run's rows on every node.

2026-10-04, Apple M4 Pro, Docker VM aarch64, shared with other workloads, no CPU/memory caps.
Two runs: the first with the image default `auto_schema=1`, the second (the committed
configuration) with `searchd_auto_schema=0`.

| | run 1 (auto_schema on) | run 2 (auto_schema off) |
|---|---|---|
| writes before the kill | 60–105k docs/s | 71–99k docs/s |
| manticore-1 sees `cluster_c_size=2` | 7.2 s after the kill | 6.7 s after the kill |
| no write acknowledged | ~18 s (16 → 34 s) | ~18 s (16 → 34 s) |
| writes while manticore-2 is down | 20–27k docs/s (every batch sent to manticore-2 fails first) | 15–26k docs/s |
| manticore-2 `synced` after `docker start` | 5.7 s | 6.3 s |
| write errors (all retried) / read errors | 451 / 751 | 478 / 353 |
| `logs` (replicated): acknowledged vs found on each node | 1,304,600 = 1,304,600 on all 3, **0 lost** | 1,142,800 = 1,142,800 on all 3, **0 lost** |
| `events` (sharded): acknowledged vs found via each node | 1,304,600 vs 1,054,901 / 912,312 / 250,400 | 1,142,800 vs 1,139,268 / 1,139,268 / 1,024,412 |

- **Replicated table: no acknowledged row lost**, every node has every row. Writes stall for
  about 18 s after a node dies (Galera has to declare it gone, and each writer also writes
  the sharded table, whose agents to the dead node time out first).
- **Sharded table: not consistent after a node failure and rejoin.** When manticore-2 died,
  the sharding master started rebalancing (`Rebalancing due to inactive nodes: 172.28.30.12:9312`),
  one step failed (`Rebalancing failed for table events: node 172.28.30.13:9312 ... status error`),
  and when manticore-2 came back a second rebalance ran (`Rebalancing due to cluster topology change`).
  Afterwards `SHOW SHARDING STATUS` reported the original layout with `rf_status ok` for all
  12 rows, but the nodes disagreed:
  - run 1: manticore-1's `events` pointed at copies on manticore-1 and -3 only; manticore-2
    still used its own shard copies, which had stopped at ~104k rows each while the others
    had ~176k; on manticore-3 the `events` wrapper had been dropped and re-created as a plain
    RT table by an `INSERT` (auto_schema): 250,400 acknowledged rows went into that local
    table, with `service`/`level` as text fields (`unable to group by stored field 'service'`).
  - run 2 (auto_schema off): those inserts failed with `table 'events' absent` and were
    retried elsewhere. Still 3,532 acknowledged rows are missing through every node, and
    manticore-3 sees 118,388 fewer.
- One run each; not investigated further. For data that must not be lost, use a replicated
  table (or `rf` copies via plain replication) rather than auto-sharding in 29.9.0.

## Scale out and in (3 → 5 → 3)

[`scripts/scale.sh`](scripts/scale.sh), with the load running (4 writers paced to 1,000 rows/s
per table, `RATE`, so five copies of `logs` stay small on a shared disk; 4 unpaced readers;
clients connect to manticore-1..3 only). `STEADY` = 30 s of load before and after each step.

| step | commands | what happens |
|---|---|---|
| `make scale-out` | `docker compose --profile scale up --wait manticore-4 manticore-5`, then on each `JOIN CLUSTER c AT 'manticore-1:9312'` | `logs` is copied to each new node by state transfer during the join; Buddy's sharding master notices the new nodes (`Rebalancing due to cluster topology change (likely new nodes)`) and moves shard copies |
| `make scale-in` | `docker compose stop manticore-5`, wait until the shard layout is stable with `rf_status ok` and no copy on that node; the same for manticore-4; `ALTER CLUSTER c UPDATE nodes`; remove the containers and volumes | the master treats a stopped node as failed and re-creates its shard copies on the remaining nodes (no graceful drain; one node at a time so `rf=2` always leaves one copy) |

2026-10-04, one run each on a fresh cluster (`make down && make up`), Apple M4 Pro, Docker VM
aarch64, shared, no caps:

| | scale-out | scale-in |
|---|---|---|
| join / stop | manticore-4 `synced` 1.8 s after `JOIN CLUSTER`, manticore-5 2.7 s; `cluster_c_size` 5 | `cluster_c_size` 4 1 s after stopping manticore-5 |
| shard layout | settled 11.3 s after the second join: manticore-1 [0,2,4], -2 [1,3,5], -3 [0,2,5], **-4 [1,3,4], -5 none** | manticore-5 held no shard: nothing moved. After stopping manticore-4, settled 17.1 s later: -1 [0,1,2,4], -2 [1,3,4,5], -3 [0,2,3,5] |
| writes (2,000 rows/s offered) | before / during / after: 2,000 / 2,000 / 1,952 rows/s; 201 failed attempts during the rebalance (`table 'events' absent`, `remote table 'system.events_s4' ... absent`), all retried | 2,000 / 2,000 / 1,825 rows/s; ~18 s with 0–400 rows/s after manticore-4 stopped; 421 failed attempts (`remote table 'system.events_s1' at 172.28.30.11:9312 failed: remote error: table 'system.events_s1' absent`), all retried |
| reads (q/s, full-text + filter + group by, growing tables) | 3,677 → 1,640 during → 1,999 after; 385 errors (`unknown local table(s) 'events' in search request`) | 1,352 → 755 during → 880 after; 34 errors |
| `logs` (replicated) acknowledged vs found | 100,800 on all 5 nodes, **0 lost** | 121,200 on all 3 nodes, **0 lost** |
| `events` (sharded) acknowledged vs found | **90,661 of 100,800 via manticore-1..4 (10,139 lost)**; manticore-5 has no `events` table (`unknown local table(s) 'events'`) | 121,200 on all 3 nodes, 0 lost |

- Query throughput falls over the run because the tables keep growing (reads scan more data);
  the readers only use manticore-1..3, so new nodes add capacity only through the shards
  they host.
- Scale-out lost acknowledged rows in the sharded table both times it ran (an earlier,
  interrupted run: 10,352 of 180,000 lost, the same layout with nothing on manticore-5). The
  moved shard was `events_s4` (manticore-3 → manticore-4); writes acknowledged by the old copy
  while it was being detached are gone.
- The fifth node got no shard and no `events` wrapper: with 6 shards x rf 2 = 12 copies, 5
  nodes would allow 3/3/2/2/2. In the first run the master logged `Queue query error: JOIN CLUSTER b81acd72... (cluster 'b81acd72...' already exists)`
  three times and then `Rebalancing completed for table events`.
- Scale-in is not a drain: a node is removed by stopping it, and its shards are rebuilt from
  the surviving copy. Two nodes at once can take both copies of a shard.
- `make scale-in` expects the state `make scale-out` leaves (5 nodes).

## Known issues

Manticore 29.9.0, Buddy 4.4.3, 2026-10-04.

- **Auto-sharding times out with the image's default listen order.** `CREATE TABLE c:events (...) shards='6' rf='2'`
  → `ERROR 1064 (42000): Waiting timeout exceeded.` (also with `timeout='90'`). The queued
  `CREATE TABLE ... type='shard'` steps stay `created` in `system.sharding_queue` and
  `system.sharding_state` has `node:127.0.0.1:9308`. Buddy's `Node::findId()` takes the first
  `listen` entry that looks like `host:port` after sorting by the number of colons; the
  image's default `9306:mysql41|...|9308:http|$ip:9312|...` makes that `9308:http`, i.e.
  `127.0.0.1:9308` on every node, which matches none of the cluster's node ids
  (`172.28.30.x:9312`). Fix: put `$ip:9312` first in `searchd_listen` (done in
  [`docker-compose.yml`](docker-compose.yml)).
- **Sharded table inconsistent after a node kill and rejoin under load**, and with
  `auto_schema` on, writes landed in an accidental local table: see [Failover](#failover).
- **Writes to sharded tables time out under load even without failures**: a few batches per
  minute fail with `remote table 'system.events_s2' at 172.28.30.13:9312 failed: connect and query timed out; processed_ids=...`
  (part of the batch was written). The client retries the whole batch with `REPLACE`.
- **No cluster prefix on writes**: `INSERT INTO logs` on a replicated table →
  `table 'logs' is a part of cluster 'c', use 'c:logs'`. Sharded tables take the plain name;
  only their DDL uses `c:`.
- **Disk full aborts nodes**: with the Docker VM disk full, manticore-2 and -3 logged
  `write error: No space left on device` on the binlog, then
  `FATAL: Failed to apply trx: ... FATAL: Node consistency compromised, aborting...` and exited
  (exit code 133); new nodes failed with `FATAL: The directory Manticore starts from must be writable for the daemon, error: /var/lib/manticore/gmb_1: write error: No space left on device`.
- Nodes are registered by IP. Restarting a container with a different IP breaks rejoin; the
  compose file pins addresses.
- A full restart of all nodes needs the most advanced node bootstrapped first
  ([Restarting a cluster](https://manual.manticoresearch.com/Creating_a_cluster/Setting_up_replication/Restarting_a_cluster));
  `make down && make up` avoids this by starting from empty volumes.

## Links

- Replication: https://manual.manticoresearch.com/Creating_a_cluster/Setting_up_replication/Setting_up_replication
- Sharded tables: https://manual.manticoresearch.com/Creating_a_table/Creating_a_sharded_table/Creating_a_sharded_table
- Sharding blog post (27.1.5): https://manticoresearch.com/blog/sharding-in-manticore-search/
- Buddy sharding code (`Node::findId`): https://github.com/manticoresoftware/manticoresearch-buddy/blob/main/src/Plugin/Sharding/Node.php
