# Tarantool

Website: https://www.tarantool.io/ · GitHub: https://github.com/tarantool/tarantool

In-memory database and Lua application server: data in memtx (RAM, persisted by WAL +
snapshots) or vinyl (LSM on disk), business logic in Lua stored procedures next to the data.

| folder | what it shows |
|---|---|
| [`single-node/`](single-node) | One instance (Tarantool 3.8.1, YAML config): memtx/vinyl spaces, indexes, stored procedures, transactions incl. a net.box stream, recovery from snapshot + WAL after `SIGKILL`, a small net.box benchmark. |

Image: `tarantool/tarantool:3.8.1` is multi-arch (amd64 + arm64) and runs natively on Apple
Silicon. 3.x tags have shipped arm64 since 3.1 (Docker Hub, checked 2026-10-04).

| [`docker-compose-cluster/`](docker-compose-cluster) | One replicaset of 3 instances (Tarantool 3 YAML config), Raft-based leader election, synchronous space; `make failover` kills the leader under write load and checks no acknowledged write is lost. |
| [`vshard-cluster/`](vshard-cluster) | Sharded cluster (vshard through the Tarantool 3 `sharding` config): 2-instance storage replicasets + router; `make scale-out` / `make scale-in` grow 2 → 3 → 2 replicasets under load with bucket rebalancing. |
| [`kubernetes-statefulset/`](kubernetes-statefulset) | 3-instance replicaset (Raft leader election) as a plain StatefulSet on kind; `make failover` force-deletes the leader pod under write load. No maintained CE operator or Helm chart for Tarantool 3. |
| [`wallet-transfers/`](wallet-transfers) | Wallet transfers in Go (go-tarantool v3): one `transfer()` stored-procedure call vs the same logic as an 8-round-trip interactive transaction vs a Valkey Lua script, 200k transfers with hot accounts, audit of sums, negative balances and idempotent replays. |

Image: `tarantool/tarantool:3.8.1` is multi-arch (amd64 + arm64) and runs natively on Apple
Silicon (Docker Hub, checked 2026-10-04).

## Benchmark

| example | date | setup | result |
|---|---|---|---|
| [single-node](single-node/README.md#benchmark) | 2026-10-04 | 1 instance, 2 CPUs / 2 GB, 64 fibers over 4 net.box connections, 10 s per op | memtx get 362k/s (p99 0.52 ms), memtx replace 231k/s, vinyl replace 94k/s (p99.9 70 ms), `transfer()` stored procedure 62.6k tx/s (p99 3.6 ms) |
| [go/lua-bench/tarantool](../go/lua-bench/tarantool) | 2026-10-04 | the go/rueidis-lua-bench workload over IPROTO (go-tarantool v3), `-n 1000000 -c 50 -keys 100000`, single node 2 CPUs / 2 GB | put 175k, get 293k, `add` 198k, `update` 111k, `delete` 187k ops/s; an earlier run on a busier VM: 61k–236k (Valkey 9.1.2 with the same flags, RESP: 407k–550k ops/s) |
| [docker-compose-cluster](docker-compose-cluster/README.md#benchmark) | 2026-10-04 | 3 instances, 2 CPUs / 1 GiB each, 64 fibers over 4 net.box connections to the leader, 10 s per op | async replace 118k/s (p99 3.0 ms), sync replace (2 of 3 WALs) 90k/s (p99 1.7 ms), get 338k/s |
| [docker-compose-cluster failover](docker-compose-cluster/README.md#failover) | 2026-10-04 | 16 writers on a sync space, leader SIGKILLed | new leader after 3.2 s, longest writer stall 3.29 s, 16 retried `Peer closed`, 0 of 1,170,927 acknowledged writes lost |
| [vshard-cluster](vshard-cluster/README.md#scaling) | 2026-10-04 | 2 → 3 → 2 storage replicasets (2 instances each) + router, 16 client fibers, ~18.7k puts/s + as many gets | scale-out: 1,000 buckets moved in 51.8 s; scale-in drain: 103.5 s; 0 failed requests, 0 of 2,256,693 acknowledged puts lost, throughput unchanged |
| [kubernetes-statefulset](kubernetes-statefulset/README.md#failover) | 2026-10-04 | 3 pods on kind, 4 writers on a sync space, leader pod force-deleted | new leader after 1.2 s, longest writer stall 0.42 s, 4 retried writes, 0 of 348,504 acknowledged writes lost |
| [wallet-transfers](wallet-transfers/README.md#results) | 2026-10-04 | 2 CPUs / 4 GB per server, 64 workers, 200k transfers, 20% on 100 hot accounts | stored procedure 65.5k tx/s (p99 2.7 ms), client-side transaction 37.2k tx/s (p99 4.0 ms, 3,592 conflict retries), Valkey Lua 149.9k tx/s (p99 1.7 ms); all audits ok |

## Known issues

- With `database.use_mvcc_engine: true`, a read-then-write procedure under the default
  `best-effort` isolation aborted ~8% of calls with `Transaction has been aborted by conflict`;
  `txn_isolation = 'read-committed'` fixed it (see [single-node](single-node/README.md#known-issues)).
- Keep hot transactions memtx-only: a vinyl statement can yield, and concurrent transactions then
  conflict.
- A replicaset scales reads and redundancy, not writes: only the leader accepts writes. Writes
  scale out with vshard ([`vshard-cluster/`](vshard-cluster)); the official image does not ship
  the vshard module.
- `tarantool/tarantool-operator` (CE) manages Cartridge clusters only; last release
  `v1.0.0-rc3` (2023-08-04). The Enterprise operator is commercial.
- With `database.use_mvcc_engine: true`, read-then-write procedures need
  `box.atomic({ txn_isolation = 'read-committed' }, …)`; the default `best-effort` aborts some
  with `Transaction has been aborted by conflict`.
- The image sets `TT_INSTANCE_NAME`; running a client script with the image needs
  `env -u TT_INSTANCE_NAME tarantool script.lua`.
