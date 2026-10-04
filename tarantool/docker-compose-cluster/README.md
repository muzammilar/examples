# Tarantool — 3-instance replicaset with leader election

One replicaset of three Tarantool 3.8.1 instances on Docker Compose: Raft-based leader election
(`replication.failover: election`), a synchronous space (commit after 2 of 3 WALs), and
`make failover`, which kills the leader under write load and checks that no acknowledged write
is lost.

## Quick start

```bash
make up         # start 3 instances, wait for a leader
make test       # replication links, 1,000 sync writes on the leader, the same rows everywhere
make failover   # write load; SIGKILL the leader; new leader; restart the old one; verify
make benchmark  # bench/bench.lua against the leader: async vs sync replace, get
make status     # per instance: election state, term, leader, replicated position, rows
make cli        # tt connect to the leader
make down       # remove containers, volumes and the network
```

## Setup

| service | image | host port | role |
|---|---|---|---|
| `tarantool-1` | `tarantool/tarantool:3.8.1` (arm64 native) | `127.0.0.1:3311` | `instance-001`, candidate |
| `tarantool-2` | same | `127.0.0.1:3312` | `instance-002`, candidate |
| `tarantool-3` | same | `127.0.0.1:3313` | `instance-003`, candidate |
| `client`, `load` (profile `test`) | same | – | `scripts/run.lua`, `scripts/load.lua` |
| `bench` (profile `bench`) | same, `cpus: 2` | – | `bench/bench.lua` |

[`app/config.yaml`](app/config.yaml), the same file in every container (`TT_INSTANCE_NAME`
selects the instance):

| setting | value | why |
|---|---|---|
| `replication.failover` | `election` | Raft; every instance is a `candidate`, the leader is the only writable one |
| `replication.timeout` | 1 s | heartbeat; a peer is considered dead after 4 x timeout |
| `replication.election_timeout` | 2 s (default 5) | time between election rounds |
| `replication.election_fencing_mode` | `soft` (default) | a leader without a quorum of connections resigns |
| `replication.synchro_quorum` | default `N / 2 + 1` = 2 | acks needed by a sync transaction |
| `replication.synchro_timeout` | 30 s (default 5) | how long a sync transaction waits for the quorum |
| `iproto.listen` | `tarantool-N:3301` | also the URI the peers replicate from |
| users | `replicator` (role `replication`), `app` / `secret` (`super`) | |

[`app/init.lua`](app/init.lua) creates the spaces whenever the instance becomes writable
(`box.watch('box.status')`); followers get them through replication. `ledger` and `kv_sync` have
`is_sync = true`; `kv_async` is a normal (asynchronous) space.

## What it does

| step | shows |
|---|---|
| [`01-replication.lua`](scripts/01-replication.lua) (leader) | `election.state = leader`, upstream/downstream `follow` to both peers, lag ~0.05 ms |
| [`02-sync-writes.lua`](scripts/02-sync-writes.lua) (leader) | 1,000 inserts into `ledger`; `box.info.synchro.quorum = 2`, queue empty afterwards |
| [`03-every-instance.all.lua`](scripts/03-every-instance.all.lua) (all) | 1,000 rows on every instance; a write on a follower fails with `Can't modify data on a read-only instance - state is election follower with term 2, leader is 1 (…)` |
| `make failover` | [`load.lua`](scripts/load.lua): 16 writers insert unique ids into `ledger` on the leader. After 10 s `docker kill --signal KILL` of the leader; writers find the new leader and retry the same id (`Duplicate key` on a retry = already committed). After 15 s more the old leader is started again. At the end every acknowledged id is looked up on the leader and the row counts of all three are compared. |

## Failover

2026-10-04, Apple M4 Pro, Docker VM aarch64, no CPU caps, 16 writers, 40 s, one run:

| | |
|---|---|
| leader killed | `tarantool-3` (`instance-003`), SIGKILL |
| new leader seen | `tarantool-2`, 3.2 s after the kill (polling `box.info.election.state`) |
| longest stall for a writer | 3.29 s |
| failed attempts (all retried) | 16, `Peer closed` (one per writer, on the killed connection) |
| acknowledged writes | 1,170,927 (29,273/s average over 40 s, sync space) |
| acknowledged writes lost | 0 |
| rows after the old leader rejoined | 1,170,927 on each of the three instances |

- The restarted instance came back as a follower in term 3 and caught up from the WAL; it was
  healthy 3 s after `docker compose up`.

## Benchmark

`make benchmark` runs [`bench/bench.lua`](bench/bench.lua) against the leader: 64 fibers over 4
net.box connections, 10 s per op, 100,000 random keys, 100-byte values.
[`bench/limits.sh`](bench/limits.sh) caps the instances at `BENCH_CPUS=6` / `BENCH_MEM=3g` in
total (2 CPUs / 1 GiB each); the client has 2 CPUs. Raw output in `results/` (gitignored).

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, shared with other agents), Tarantool
3.8.1, one run, `ledger` already holding 1.17M rows from the failover run:

| op | ops/s | p50 ms | p99 ms | p99.9 ms | max ms |
|---|---:|---:|---:|---:|---:|
| `async_replace` (`kv_async`, commit after the leader's WAL) | 118,211 | 0.376 | 2.967 | 7.951 | 71.9 |
| `sync_replace` (`kv_sync`, commit after 2 of 3 WALs) | 89,758 | 0.665 | 1.668 | 3.400 | 7.2 |
| `get` (on the leader) | 337,634 | 0.165 | 0.440 | 1.485 | 22.6 |

- Sync vs async replace: 89,758 vs 118,211 ops/s (0.76x); p50 0.665 vs 0.376 ms; p99 1.668 vs
  2.967 ms; max 7.2 vs 71.9 ms. One run each; the lower sync tail was not investigated.

## Known issues

- Leader election and the sync quorum assume the default `synchro_quorum` (`N / 2 + 1`). The
  docs: lowering it below that breaks elections; for no data loss use only synchronous spaces
  ([leader election](https://www.tarantool.io/en/doc/latest/platform/replication/repl_leader_elect/)).
- After `docker compose restart` of all three at once the instances needed a few seconds
  (term 2 → 3) before one became leader; `make up` waits up to 30 s for one.
- During a first attempt the Docker VM disk filled up (other workloads on the shared VM): every
  instance logged `can't allocate disk space: No space left on device` for its `.xlog`, writes
  failed with `… synchro queue with term 4 belongs to 2 (…) and is frozen until promotion`, and
  no new leader was elected for the remaining 30 s. Not a Tarantool bug, but it shows that a
  full WAL disk stops the replicaset.
- `scripts/run.lua`, `load.lua` and `bench.lua` end with `os.exit()`: with open net.box
  connections a `tarantool` script never exits, and its stdout (not a TTY) is only flushed at exit.

## Links

- Leader election: https://www.tarantool.io/en/doc/latest/platform/replication/repl_leader_elect/
- Synchronous replication: https://www.tarantool.io/en/doc/latest/platform/replication/repl_sync/
- Configuration reference (`replication.*`): https://www.tarantool.io/en/doc/latest/reference/configuration/configuration_reference/
