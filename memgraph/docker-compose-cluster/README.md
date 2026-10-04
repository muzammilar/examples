# Memgraph — replication cluster (Community)

One MAIN and two REPLICAs (one SYNC, one ASYNC) on Docker Compose, with Memgraph Community's
replication. `make failover` kills the MAIN under write load and does the failover by hand
(promote a replica, re-point the other, bring the old MAIN back), counting what was and was not
replicated. `make scale-demo` adds and removes read replicas under read load. Automatic failover
needs Enterprise coordinators; `make enterprise-ha-check` shows the refusal.

## Quick start

```bash
make up                   # 3 instances, setup one-shot (roles + REGISTER REPLICA), builds the Go client
make test                 # write on MAIN, read on both replicas, a write on a replica is refused
make failover             # kill MAIN under load, manual promotion, old MAIN rejoins (then: make down && make up)
make verify               # every acknowledged write of the last failover run, per instance
make scale-out            # + memgraph-replica-3 and -4 (ASYNC), wait until each is ready
make scale-in             # DROP REPLICA, remove them
make scale-demo           # read load for 180 s while scaling 2 -> 4 -> 2 replicas
make enterprise-ha-check  # one coordinator without a license: SHOW INSTANCES is refused
make status               # roles, SHOW REPLICAS on the MAIN
make cli                  # mgconsole on memgraph-main
make down                 # everything, including volumes and the client image
```

## Setup

| service | container | image | host port | role |
|---|---|---|---|---|
| `main` | `memgraph-main` | `memgraph/memgraph:3.13.1` | `127.0.0.1:7690` | MAIN |
| `replica1` | `memgraph-replica-1` | same | `127.0.0.1:7691` | REPLICA, registered `SYNC`, replication port 10000 |
| `replica2` | `memgraph-replica-2` | same | `127.0.0.1:7692` | REPLICA, registered `ASYNC` |
| `replica3`, `replica4` (profile `scale`) | `memgraph-replica-3/-4` | same | — | read replicas for `scale-*`, `ASYNC` |
| `setup` | one-shot | same | — | [`scripts/setup.sh`](scripts/setup.sh): `SET REPLICATION ROLE TO REPLICA WITH PORT 10000`, `REGISTER REPLICA ... SYNC/ASYNC TO ...` (idempotent) |
| `client` (profile `tools`) | — | built from [`client/`](client) | — | Go, `neo4j-go-driver/v6` 6.3.0 over Bolt |
| `coordinator` (profile `ha`) | `memgraph-coordinator-1` | `memgraph/memgraph:3.13.1` | — | `--coordinator-id=1 ...` for `enterprise-ha-check` only |
| `sysctl` | one-shot, privileged | `busybox:1.37` | — | `vm.max_map_count` to 524288 if lower (whole Docker VM / Linux host) |

- Every instance: `cpus: 1` (`MEMGRAPH_CPUS`), `--memory-limit=1024` MiB, WAL on,
  `--replication-restore-state-on-startup=true` (the default: an instance restarts with the role
  and replica list it had), telemetry off. No auth, so ports bind to 127.0.0.1.
- Modes ([docs](https://memgraph.com/docs/clustering/replication)): `SYNC` = MAIN waits for the
  replica before it acknowledges a commit, but still commits if the replica is down; `ASYNC` =
  MAIN does not wait; `STRICT_SYNC` = two-phase commit, MAIN cannot commit while the replica is
  down (not used here).

## What it does

### `make test`

- [`cypher/01-main-write.cypher`](cypher/01-main-write.cypher) on MAIN: index, 1,000 accounts,
  5,000 `PAID` edges, `SHOW REPLICAS` (`{memgraph: {behind: 0, status: "ready", ts: 7}}` for both).
- [`cypher/02-replica-read.cypher`](cypher/02-replica-read.cypher) on each replica: role `replica`,
  same counts (1000 / 5000 / 127500), the index exists there too.
- A `CREATE` on a replica: `Write queries are forbidden on the replica instance. Replica instances
  accept only read queries, while the main instance accepts read and write queries.`

### `make failover`

[`scripts/failover.sh`](scripts/failover.sh), with [`client write`](client/main.go): 4 workers,
45 s, one `CREATE (:Tick {seq})` per auto-commit query to whichever host accepts writes; a failed
write is retried with the same `seq` on the next host; every acknowledged `seq` is logged.

| step | what |
|---|---|
| t=10 s | `docker kill --signal KILL memgraph-main` |
| +1 s | `client verify` on both replicas against the acknowledged writes |
| | `SET REPLICATION ROLE TO MAIN` on `memgraph-replica-1`, `REGISTER REPLICA replica2 ASYNC TO 'memgraph-replica-2:10000'` (retried, see Known issues) |
| +5 s | `docker start memgraph-main`: it restores its old role and comes back as a second MAIN; a `CREATE (:Stray)` on it succeeds |
| | `SET REPLICATION ROLE TO REPLICA` on it and `REGISTER REPLICA replica0 SYNC` on the new MAIN: refused (6 tries) |
| | old MAIN: container and volume removed, started empty, set to REPLICA, registered SYNC, initial sync |
| end | `client verify` on all three |

### `make scale-out` / `make scale-in` / `make scale-demo`

Memgraph does not shard: every instance holds the whole graph. Adding instances adds read
capacity only; writes all go to the one MAIN. Community has no read routing either (Bolt routing
needs the Enterprise coordinators), so [`client read`](client/read.go) routes reads itself: it
reads `SHOW REPLICAS` on the MAIN every 500 ms and round-robins over the replicas whose
`data_info` status is `ready`.

`scale-demo`: seeds 20,000 `:Person` and 200,000 `:KNOWS` on MAIN, then for 100 s 16 readers run
2-hop counts (`MATCH (:Person {id: $id})-[:KNOWS]->()-[:KNOWS]->(c) RETURN count(DISTINCT c)`)
on the replicas while one writer adds up to 200 `:KNOWS`/s on MAIN; at t=20 s `scale-out` (2 → 4
replicas, 3 → 5 instances), 40 s after it finishes `scale-in` (4 → 2); 180 s in total.

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24.4 GB, Docker 29.5.3), Memgraph 3.13.1
native arm64, every instance capped at 1 CPU, one run each, Docker VM shared with other agents'
containers (and its disk ran full during two earlier attempts, see Known issues).

### Failover (`make failover`)

| | result |
|---|---|
| writes before the kill | 4,436 acknowledged in 10 s, 397–533/s (4 workers, SYNC + ASYNC replica) |
| at the kill, `memgraph-replica-1` (SYNC) | 0 acknowledged writes missing; 1 write present that was never acknowledged (committed, ack lost in the kill) |
| at the kill, `memgraph-replica-2` (ASYNC) | 7 acknowledged writes missing (4,429 of 4,436) |
| manual promotion | `SET REPLICATION ROLE TO MAIN` + `REGISTER REPLICA replica2` took 0.28 s; done 1.97 s after the kill |
| longest gap between two acknowledged writes | 1,891 ms (from t=9.8 s), 287 failed attempts retried (DNS `no such host` for the killed MAIN, `Write queries are forbidden on the replica instance` from the replicas) |
| writes after promotion | 1,797/s in the first second (replica-2 still in `recovery`, nothing to wait for), then 350–600/s, 35–190/s while the old MAIN did its initial sync |
| old MAIN restarted | role `main` (two MAINs); `CREATE (:Stray)` accepted on it; its replica entries `status: "invalid"` |
| old MAIN demoted + registered | refused 6 times (`Error: 6`, `Error: 3`, `Error: 6`); `:Stray` only on the old MAIN |
| old MAIN wiped and rejoined | registered after 15 attempts (8.45 s, under load), rejoined as SYNC replica 21.6 s after the wipe started |
| end (18,781 acknowledged writes) | all three instances: 0 missing, 0 never-acknowledged, 1 duplicate `seq` (the in-flight write above, retried) |

### Read replicas (`make scale-demo`)

16 readers (2-hop count on 20k persons / 200k edges), round-robin over the registered replicas;
1 writer on MAIN. Rows are consecutive phases (number of replicas the client was reading from).

| replicas in rotation | seconds | reads/s | p50 ms | p99 ms | failed reads |
|---:|---:|---:|---:|---:|---:|
| 2 | 46 | 5,048 | 0.39 | 82.23 | 0 |
| 3 | 7 | 9,214 | 0.31 | 68.23 | 0 |
| 4 | 69 | 11,934 | 0.30 | 40.04 | 0 |
| 3 | 43 | 9,443 | 0.30 | 52.73 | 0 |
| 2 | 15 | 5,710 | 0.35 | 83.13 | 0 |

| step | command | took |
|---|---|---|
| scale-out 2 → 4 replicas (3 → 5 instances) | `docker compose --profile scale up --wait`, `SET REPLICATION ROLE TO REPLICA WITH PORT 10000`, `REGISTER REPLICA replicaN ASYNC TO ...` | containers healthy 6.3 s; `REGISTER` needed 33 and 14 attempts (`Error: 6`); registered and caught up (200,491 `:KNOWS` on each) 27.1 s later; 33.5 s in total |
| scale-in 4 → 2 | `DROP REPLICA replicaN`, remove containers and volumes | `DROP REPLICA` needed 47 and 77 attempts (`Failed to unregister replica due to lack of unique access over the cluster state. Please try again later on.`); 72.6 s in total |

- Reads: 5,048 → 9,214 → 11,934 reads/s with 2 → 3 → 4 replicas at 1 CPU each (4 vs 2 replicas:
  2.36x), then 9,443 and 5,710 reads/s after scale-in. No failed reads: the client stops using a replica once `DROP REPLICA`
  removes it from `SHOW REPLICAS`, before the container goes away.
- p99 of 40–83 ms with p50 0.3 ms is CPU throttling: each replica has a 1-CPU quota and 16 readers.
- Writes on MAIN: 1,799 in 180 s (the writer targets 200/s). MAIN waits for the SYNC replica on
  every commit, and that replica is also serving reads at its CPU limit.
- An earlier attempt (same day) did not retry `DROP REPLICA`: the replicas were removed while still
  registered and 300 reads failed; its numbers are not used.

## Known issues

- **No automatic failover in Community.** A coordinator without a license:
  `Access to high availability requires an enterprise, ai_platform, or oem license.`
  ([HA docs](https://memgraph.com/docs/clustering/high-availability); the trial needs a form at
  https://memgraph.com/enterprise-trial). Detection, promotion, re-pointing replicas and fencing
  the old MAIN are all manual here.
- **A restarted MAIN comes back as MAIN** (`--replication-restore-state-on-startup=true`, the
  default) and accepts writes: two MAINs. Its old replica entries show
  `status: "invalid"`; it did not take back `memgraph-replica-2` in this run, but nothing stops
  clients that still point at it. Fence it (keep it stopped, or start it with
  `--replication-restore-state-on-startup=false` and demote it; not tested here) before it
  serves clients.
- **A diverged old MAIN cannot rejoin.** After its stray write, `REGISTER REPLICA replica0 SYNC TO
  'memgraph-main:10000'` on the new MAIN fails (`Couldn't register replica replica0. Error: 6`,
  then `Error: 3`). Dropping its data and starting it empty works; the stray write is gone.
- **`REGISTER REPLICA` fails intermittently under write load** with `Couldn't register replica
  <name>. Error: 6`. In 3.13.1 the 7th value of `RegisterReplicaError` is `NO_ACCESS`, returned
  when `TryLock` on the replication state fails (`src/query/replication_query_handler.hpp`,
  `src/replication_handler/replication_handler.cpp`). `Error: 3` is `CONNECTION_FAILED` (replica
  not listening yet). The scripts retry every 0.5 s. In one earlier run the promotion's
  `REGISTER REPLICA replica2` failed once and was not retried: `memgraph-replica-2` then stayed
  without a MAIN and missed 86,447 acknowledged writes, while writes ran at 1,989/s instead of
  ~420/s (no replica to wait for).
- **Retried writes can be duplicated.** A write that commits on MAIN and the SYNC replica but whose
  acknowledgement is lost in the kill is retried on the new MAIN: one `seq` ended up twice.
  Non-idempotent `CREATE`s need a key and `MERGE`, or a uniqueness constraint.
- **ASYNC replicas lag.** At the kill, `memgraph-replica-2` (ASYNC) missed 1 and 7 acknowledged
  writes in two of four runs; `memgraph-replica-1` (SYNC) missed none in all four.
- **`REGISTER` / `DROP REPLICA` contend with load.** In `scale-demo` they needed up to 77 retries
  (0.5 s apart). `DROP REPLICA` fails with `Failed to unregister replica due to lack of unique
  access over the cluster state. Please try again later on.`
- **Replica status flips under writes.** An ASYNC replica a few commits behind cycles through
  `ready`, `replicating`, `recovery` and `invalid` in `SHOW REPLICAS`, with negative `behind`
  (`{behind: -4, status: "recovery", ...}`). The read client therefore uses every registered
  replica (stale reads possible) and `scale-out` waits on edge counts, not on the status.
- **Disk full kills Memgraph.** Twice the shared Docker VM's disk filled up (other workloads):
  `Assertion failed in file /home/mg/memgraph/src/utils/file.cpp at line 657. Expression:
  'written > 0' ... No space left on device (28)`, and the process exits (code 133) rather than
  refusing writes.
- mgconsole `--output-format=csv` doubles quotes in values (`"""main"""`); the Makefile strips them.

## Links

- Replication: https://memgraph.com/docs/clustering/replication
- Replication commands: https://memgraph.com/docs/clustering/replication/replication-commands-reference
- High availability (Enterprise): https://memgraph.com/docs/clustering/high-availability
- Go driver: https://github.com/neo4j/neo4j-go-driver
