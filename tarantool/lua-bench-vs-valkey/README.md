# Tarantool — Lua benchmark vs Valkey at matching durability

The [`go/rueidis-lua-bench`](../../go/rueidis-lua-bench) workload (SET/GET plus three Lua scripts
on a versioned record) on Valkey 9.1.2 and its Tarantool 3.8.1 port
[`go/lua-bench/tarantool`](../../go/lua-bench/tarantool), with both servers set to the same
durability: no persistence, OS page cache, ~1 s, fsync per commit. Then the same comparison on a
3-node replicated setup (async and sync) and on a 3-shard setup. Each setup runs on fresh
containers, one at a time.

## Quick start

```sh
make up                 # build both clients; 1 Tarantool (wal.mode write) + 1 Valkey (no AOF)
make test               # both clients, -n 20000; exit non-zero on an error or CAS mismatch
make benchmark          # bench-single + bench-replicated + bench-sharded, results/<setup>.txt
make sync-wal-none      # sync replicaset with wal.mode none: shows that it does not replicate
make table T=single-tarantool-write V=single-valkey-everysec   # Markdown rows for two results
make status / make cli / make cli-valkey
make down               # containers, volumes, network and the three built images
```

## Setup

All servers: 2 CPUs, 2 GiB (`cpus`, `mem_limit` in [`docker-compose.yml`](docker-compose.yml)).
Clients run in containers on the same Docker network with no CPU cap.

| profile | services | image | role |
|---|---|---|---|
| `single-tarantool` | `tarantool` | `tarantool/tarantool:3.8.1` | 1 instance, [`tarantool/single/config.yaml`](tarantool/single/config.yaml) |
| `single-valkey` | `valkey` | `valkey/valkey:9.1.2` | 1 instance, `--io-threads 2` |
| `rs-tarantool` | `tarantool-1..3` | `tarantool/tarantool:3.8.1` | 1 replicaset, Raft election; only `tarantool-1` is a candidate, so it is the leader ([`tarantool/replicaset/`](tarantool/replicaset)) |
| `rs-valkey` | `valkey-primary`, `valkey-replica-1..2` | `valkey/valkey:9.1.2` | primary + 2 replicas (`--replicaof`), async replication |
| `shard-tarantool` | `tarantool-storage-a..c`, `tarantool-router` | `tarantool-lua-bench-vshard:local` ([`../vshard-cluster/Dockerfile`](../vshard-cluster/Dockerfile): 3.8.1 + vshard 0.1.42) | 3 storage replicasets of 1 instance each + 1 router, 3,000 buckets ([`tarantool/vshard/`](tarantool/vshard)) |
| `shard-valkey` | `valkey-shard-1..3` (+ `valkey-shard-init`) | `valkey/valkey:9.1.2` | cluster, 3 primaries, no replicas |
| `tools` | `lua-bench-tarantool`, `lua-bench-valkey` | built from `go/lua-bench/tarantool`, `go/rueidis-lua-bench` | the clients |

Durability settings (verified against Tarantool 3.8.1's config schema, `wal.mode` enum
`none | write | fsync`, and Valkey 9.1.2's `valkey.conf`):

| level | Tarantool `wal.mode` | Valkey | what an acknowledged write survives |
|---|---|---|---|
| none | `none` | `--appendonly no` | nothing (RAM only) |
| OS cache | `write` | `--appendonly yes --appendfsync no` | a process crash, not an OS crash / power loss |
| ~1 s | `write` (Tarantool has no periodic fsync mode) | `--appendonly yes --appendfsync everysec` | Valkey: loses up to ~1 s on power loss; Tarantool: same as the row above |
| every commit | `fsync` | `--appendonly yes --appendfsync always` | power loss (fsync before the reply; both group-commit concurrent writes) |

- `bench/run.sh` sets them per setup: `WAL_MODE` → `TT_WAL_MODE` (not set in the YAML, so the
  env var applies), `VALKEY_PERSIST` → valkey-server flags. Each run prints the values read back
  from the server (`box.cfg.wal_mode`, `CONFIG GET appendonly appendfsync save`).
- RDB is off on every Valkey (`--save ""`). Tarantool's checkpoint (every hour) never fires during a run.
- Synchronous Tarantool spaces (`*-sync-*`): [`tarantool/replicaset/init.lua`](tarantool/replicaset/init.lua)
  creates `bench_s` and `bench_h` with `is_sync = true` before the client starts; a commit returns
  once 2 of 3 instances (quorum N/2+1) have it in their WAL.
- `WAIT 1 0` (`*-wait1`): `go/rueidis-lua-bench -wait 1` sends `WAIT 1 0` with every write in the
  same round trip and fails if fewer than 1 replica acknowledged. A replica acknowledges once it
  has applied the write in memory; it does not wait for the replica's AOF.
- `*-tmpfs-*`: [`docker-compose.tmpfs.yml`](docker-compose.tmpfs.yml) puts `/var/lib/tarantool`
  (WAL, snapshots) on a 1 GiB tmpfs instead of a volume, so a commit confirmed by the quorum
  exists only in the RAM of the instances that confirmed it.

## What it does

`-n 1000000 -c 50 -keys 100000`: 1,000,000 operations per op, 50 workers, 100,000 keys; worker
`w` owns 2,000 keys and cycles through them.

| op | Tarantool | Valkey | writes |
|---|---|---|---|
| put / SET | `REPLACE` into `bench_s` | `SET` | every op |
| get / GET | `SELECT` by key | `GET` | none |
| add | `CALL bench_add` | `add.lua` | first 100,000 ops (create); the other 900,000 find the key and return 0 |
| update | `CALL bench_update` (CAS on version) | `update.lua` | every op |
| delete | `CALL bench_delete` | `delete.lua` | first 100,000 ops; the rest find nothing |

- put and update write on every op; add and delete write on 10% of their ops, so they show the
  durability cost only partly.
- Tarantool: go-tarantool v3 multiplexes all workers over one IPROTO connection. Valkey: rueidis
  auto-pipelines over one connection per node, except with `-wait` (WAIT blocks the connection, so
  each worker's script or SET + WAIT goes on its own dedicated connection, also for the add and
  delete calls that write nothing).
- Sharded Tarantool: the client calls `bench_*` functions on the router
  (`go/lua-bench/tarantool -router`), which forward to the storage owning the key's bucket with
  `vshard.router.callrw` ([`tarantool/vshard/router.lua`](tarantool/vshard/router.lua),
  [`storage.lua`](tarantool/vshard/storage.lua)). rueidis in cluster mode sends each command
  straight to the primary that owns the slot. So Tarantool has one extra hop and all traffic
  through one 2-CPU router; Valkey has none.

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (11 CPUs, 24.4 GiB; no other agents running),
Tarantool 3.8.1, Valkey 9.1.2, every server 2 CPUs / 2 GiB, clients uncapped on the same Docker
network, `-n 1000000 -c 50 -keys 100000`, one run per setup. Ratio = Tarantool ops/s / Valkey ops/s.

### Single node

| level | op | Tarantool ops/s | p50 ms | p99 ms | Valkey ops/s | p50 ms | p99 ms | ratio |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| none (`none` / no AOF) | put / SET | 288,157 | 0.17 | 0.27 | 525,143 | 0.09 | 0.20 | 0.55x |
| | get / GET | 277,477 | 0.17 | 0.33 | 538,615 | 0.09 | 0.19 | 0.52x |
| | add | 217,329 | 0.20 | 0.56 | 485,277 | 0.10 | 0.21 | 0.45x |
| | update | 167,463 | 0.27 | 0.85 | 393,023 | 0.12 | 0.23 | 0.43x |
| | delete | 263,966 | 0.17 | 0.48 | 493,906 | 0.10 | 0.22 | 0.53x |
| OS cache (`write` / `appendfsync no`) | put / SET | 214,707 | 0.22 | 0.35 | 520,961 | 0.09 | 0.21 | 0.41x |
| | get / GET | 298,398 | 0.16 | 0.25 | 555,611 | 0.09 | 0.19 | 0.54x |
| | add | 211,400 | 0.21 | 0.56 | 484,648 | 0.10 | 0.21 | 0.44x |
| | update | 119,978 | 0.37 | 1.01 | 320,291 | 0.15 | 0.27 | 0.37x |
| | delete | 249,134 | 0.17 | 0.56 | 511,998 | 0.09 | 0.20 | 0.49x |
| ~1 s (`write` / `everysec`) | put / SET | 214,707 | 0.22 | 0.35 | 531,618 | 0.09 | 0.20 | 0.40x |
| | get / GET | 298,398 | 0.16 | 0.25 | 556,984 | 0.09 | 0.19 | 0.54x |
| | add | 211,400 | 0.21 | 0.56 | 469,865 | 0.10 | 0.24 | 0.45x |
| | update | 119,978 | 0.37 | 1.01 | 329,736 | 0.15 | 0.26 | 0.36x |
| | delete | 249,134 | 0.17 | 0.56 | 505,372 | 0.10 | 0.21 | 0.49x |
| every commit (`fsync` / `always`) | put / SET | 67,365 | 0.72 | 1.39 | 49,517 | 0.98 | 1.63 | 1.36x |
| | get / GET | 301,602 | 0.16 | 0.26 | 557,898 | 0.09 | 0.19 | 0.54x |
| | add | 197,704 | 0.20 | 0.70 | 260,106 | 0.10 | 1.30 | 0.76x |
| | update | 79,728 | 0.57 | 1.21 | 45,890 | 1.06 | 1.69 | 1.74x |
| | delete | 234,270 | 0.17 | 0.72 | 261,039 | 0.10 | 1.29 | 0.90x |

The Tarantool `write` run is used for both the OS-cache and the ~1 s rows (one run).

- Without fsync (`none`, OS cache, ~1 s), Valkey was 1.8x–2.7x Tarantool on every op (put
  520,961 vs 214,707 ops/s at OS cache, 2.4x).
- With fsync per commit, Tarantool was ahead on the two write-every-op ops: put 67,365 vs
  49,517 ops/s (1.36x), update 79,728 vs 45,890 ops/s (1.74x), p99 1.39 ms vs 1.63 ms and
  1.21 ms vs 1.69 ms.
- Cost of fsync per commit on put: Tarantool 214,707 → 67,365 ops/s (-69%), Valkey 531,618 →
  49,517 ops/s (-91%).
- Turning the WAL on (`none` → `write`) cost Tarantool 25% on put (288,157 → 214,707 ops/s)
  and 28% on update; AOF without fsync cost Valkey 1% on SET and 18.5% on update.lua.

### Replicated: 3 nodes, writes to the leader / primary

| setup | op | Tarantool ops/s | p50 ms | p99 ms | Valkey ops/s | p50 ms | p99 ms | ratio |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| async, `write` / `everysec` | put / SET | 167,620 | 0.29 | 0.44 | 383,938 | 0.12 | 0.29 | 0.44x |
| | get / GET | 300,977 | 0.16 | 0.27 | 538,536 | 0.09 | 0.21 | 0.56x |
| | add | 207,781 | 0.20 | 0.61 | 466,129 | 0.10 | 0.24 | 0.45x |
| | update | 101,163 | 0.45 | 1.11 | 261,436 | 0.18 | 0.37 | 0.39x |
| | delete | 243,134 | 0.17 | 0.63 | 480,263 | 0.10 | 0.23 | 0.51x |
| async, `fsync` / `always` | put / SET | 53,572 | 0.85 | 1.73 | 29,352 | 1.66 | 2.75 | 1.83x |
| | get / GET | 298,812 | 0.16 | 0.25 | 542,310 | 0.09 | 0.20 | 0.55x |
| | add | 171,618 | 0.20 | 1.17 | 185,589 | 0.11 | 2.27 | 0.92x |
| | update | 53,364 | 0.87 | 1.69 | 27,149 | 1.79 | 2.74 | 1.97x |
| | delete | 198,830 | 0.17 | 1.21 | 193,932 | 0.10 | 2.16 | 1.03x |
| sync, `write` / `everysec` + `WAIT 1 0` | put / SET | 83,170 | 0.59 | 0.84 | 90,685 | 0.52 | 1.04 | 0.92x |
| | get / GET | 302,736 | 0.16 | 0.27 | 547,222 | 0.09 | 0.19 | 0.55x |
| | add | 182,597 | 0.20 | 0.86 | 182,901 | 0.24 | 0.81 | 1.00x |
| | update | 60,452 | 0.78 | 1.64 | 85,644 | 0.55 | 1.05 | 0.71x |
| | delete | 211,914 | 0.17 | 0.93 | 186,188 | 0.24 | 0.75 | 1.14x |

Memory only ("sync replication, no disk"): Tarantool WAL on tmpfs vs Valkey without AOF.

| setup | op | Tarantool ops/s | p50 ms | p99 ms | Valkey ops/s | p50 ms | p99 ms | ratio |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| async, tmpfs `write` / no AOF | put / SET | 167,082 | 0.29 | 0.47 | 397,783 | 0.12 | 0.26 | 0.42x |
| | get / GET | 300,794 | 0.16 | 0.27 | 534,517 | 0.09 | 0.21 | 0.56x |
| | add | 208,018 | 0.20 | 0.61 | 471,996 | 0.10 | 0.23 | 0.44x |
| | update | 103,164 | 0.44 | 1.10 | 285,674 | 0.16 | 0.42 | 0.36x |
| | delete | 242,548 | 0.17 | 0.64 | 488,240 | 0.10 | 0.22 | 0.50x |
| sync, tmpfs `write` / no AOF + `WAIT 1 0` | put / SET | 83,046 | 0.59 | 0.84 | 90,316 | 0.53 | 1.03 | 0.92x |
| | get / GET | 307,631 | 0.16 | 0.28 | 544,029 | 0.09 | 0.20 | 0.57x |
| | add | 185,533 | 0.20 | 0.84 | 184,224 | 0.24 | 0.79 | 1.01x |
| | update | 65,792 | 0.72 | 1.39 | 88,032 | 0.54 | 1.04 | 0.75x |
| | delete | 207,898 | 0.17 | 0.94 | 187,717 | 0.24 | 0.72 | 1.11x |

- Synchronous spaces halved Tarantool's put (167,620 → 83,170 ops/s, p99 0.44 → 0.84 ms);
  `WAIT 1 0` cut Valkey's SET by 4.2x (383,938 → 90,685 ops/s). With a replica ack on every
  write they ended close: put 83,170 vs 90,685 ops/s (0.92x), update 60,452 vs 85,644 ops/s (0.71x).
- tmpfs made no difference with `wal.mode: write`: async put 167,082 vs 167,620 ops/s, sync put
  83,046 vs 83,170 ops/s. `write` does not wait for the disk, so the page cache absorbs it
  either way; the sync cost is the replication round trip and the quorum, not the disk.
- With fsync on all three nodes, Tarantool was 1.83x on put (53,572 vs 29,352 ops/s) and 1.97x
  on update. From single node to 3 nodes, Tarantool `fsync` put fell 20% (67,365 → 53,572 ops/s),
  Valkey `always` SET 41% (49,517 → 29,352 ops/s).
- Adding 2 async replicas cost Tarantool 22% on put (214,707 → 167,620 ops/s) and Valkey 28% on
  SET (531,618 → 383,938 ops/s, `everysec`).

Sync replication with `wal.mode: none` (`make sync-wal-none`): not possible. Tarantool
replicates from its WAL; a node with `wal.mode: none` refuses replicas
([`box_connect_replica`](https://github.com/tarantool/tarantool/blob/3.8.1/src/box/box.cc) in
3.8.1). What happened:

- `tarantool-2` and `tarantool-3` log `Replication does not support wal_mode = 'none'` and
  `rebootstrap failed, will retry every 1.00 second`; connecting to them fails with
  `Instance bootstrap hasn't finished yet`.
- `tarantool-1` becomes leader alone: `box.info.synchro.quorum` = 1, `#box.info.replication` = 1.
  A write to an `is_sync` space committed in 0.03 ms with no replica, so "synchronous" gives no
  replica guarantee in this setup.
- The memory-only replicated setup is therefore `wal.mode: write` with the WAL on tmpfs (table above).

### Sharded: 3 shards, no replicas, `write` / `everysec`

| op | Tarantool vshard ops/s | p50 ms | p99 ms | Valkey cluster ops/s | p50 ms | p99 ms | ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| put / SET | 92,004 | 0.50 | 1.48 | 437,769 | 0.10 | 0.31 | 0.21x |
| get / GET | 116,945 | 0.39 | 1.34 | 450,905 | 0.10 | 0.30 | 0.26x |
| add | 110,315 | 0.41 | 1.41 | 407,041 | 0.11 | 0.32 | 0.27x |
| update | 83,655 | 0.55 | 1.63 | 396,351 | 0.11 | 0.33 | 0.21x |
| delete | 113,014 | 0.39 | 1.41 | 422,326 | 0.10 | 0.31 | 0.27x |

- Tarantool: 3 storages + 1 router (8 CPUs); every request goes client → router → storage.
  Valkey: 3 primaries (6 CPUs), client → owning primary.
- Through the router, Tarantool was slower than its single node on every op (put 92,004 vs
  214,707 ops/s, get 116,945 vs 298,398). Valkey cluster vs single node: SET 437,769 vs 531,618,
  update.lua 396,351 vs 329,736 ops/s. Which part of the Tarantool path saturates (the one router
  or the extra hop) was not measured; more routers, or a client that routes itself, were not tried.

## Known issues

- Valkey's default `save 3600 1 300 100 60 10000` is on unless `--save ""` is given; the earlier
  [`valkey/minimal-lua`](../../valkey/minimal-lua) numbers ran with it (no AOF, RDB on).
- The Docker VM's disk is a virtual disk on the Mac's SSD; fsync cost here says nothing about a
  server disk with a power-loss-protected cache.

## Links

- Tarantool `wal.mode`: https://www.tarantool.io/en/doc/latest/reference/configuration/configuration_reference/
- Tarantool synchronous replication: https://www.tarantool.io/en/doc/latest/platform/replication/repl_sync/
- Valkey persistence: https://valkey.io/topics/persistence/
- Valkey `WAIT`: https://valkey.io/commands/wait/
