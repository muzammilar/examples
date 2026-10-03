# TigerBeetle — standbys, replica replacement and rolling restarts

Standbys, replica replacement and rolling restarts on a running 3-replica TigerBeetle cluster
(Docker Compose), each step under load.

## Quick start

```bash
make up               # replica-0..2 (format on first start), cache-grid 256MiB
make test             # client/demo.py
make scale-out        # under load: actives get 5 addresses one at a time, then standby-3 + standby-4 start
make standby          # stop 2 of 3 actives: the 2 standbys do not keep the cluster available
make scale-in         # under load: remove both standbys and their volumes, actives back to 3 addresses
make replace          # under load: delete a backup's container + volume, `tigerbeetle recover`, state sync (R=<index>)
make rolling-restart  # under load: recreate every replica one at a time (backups, standbys, primary last)
make benchmark        # tigerbeetle benchmark, 1M transfers against the 3 actives (BENCH_ARGS=...)
make status           # services, each replica's last view change (role: primary / backup)
make cli              # tigerbeetle repl
make down             # remove containers, volumes and the client image
```

What TigerBeetle 0.17.9 lets you change on a running cluster, run on Docker Compose: three active
replicas (`--replica-count=3`), two optional standbys, and a load client
([`client/load.py`](client/load.py)) during every step. The load client sends batches of 100
transfers back to back. At the end it checks that the sink account's balance equals the number
of acked transfers.

**What cannot be done:** the number of active replicas is fixed when the data files are
formatted. You cannot go from 3 to 5 voting replicas, or from 6 to 3, without formatting a new
cluster and moving the data over. The protocol has a `reconfigure` operation, but in 0.17.9 it
rejects any change to the replica or standby count (`different_replica_count`,
`different_standby_count` in `src/vsr.zig`), and no client or CLI command sends it. A standby
cannot be promoted to an active replica. "Scaling" here means adding and removing standbys,
which never vote, and changing per-replica resources with a rolling restart.

The steps live in [`ops.sh`](ops.sh). `DURATION` (default 40 s) sets how long the load runs;
its output goes to `results/` (gitignored). `rolling-restart` picks up whatever changed in the
environment: `CACHE_GRID=2GiB MEM_LIMIT=6g make rolling-restart` resizes the grid cache. A new
`TIGERBEETLE_VERSION` upgrades the cluster the same way, which the
[upgrade docs](https://docs.tigerbeetle.com/operating/upgrading/#upgrading-docker-based-installations)
describe; that was not run here.

- **Standbys** ([experimental](https://github.com/tigerbeetle/tigerbeetle/blob/0.17.9/src/tigerbeetle/cli.zig#L42):
  "standbys don't have a concrete practical use-case yet") are formatted with
  `--standby=<index>` (index ≥ replica count, at most 6 standbys). They are listed in every
  replica's `--addresses` after the actives. They receive and commit the log, but they do not
  count toward any quorum. Clients list only the actives. Adding or removing a standby changes
  the address list, so every active needs a rolling restart. `ops.sh` passes the 3- or
  5-address list as `ADDRESSES`. A plain `make up` after `scale-out` would recreate the actives
  with 3 addresses.
- **Replacing a replica** whose data file is lost:
  [`tigerbeetle recover`](https://docs.tigerbeetle.com/operating/recovering/), never `format`.
  A re-formatted replica could forget promises it made and lose committed data. `recover`
  writes a data file that must state-sync from the others before it takes part in consensus.
  It needs a healthy cluster. The service runs it when `RECOVER=1` and `/data` is empty.
- **Resources:** `--cache-grid` and the memory limit are start-time flags, not part of the data
  file, so a rolling restart changes them. All replicas should use the same batch size; mixing
  in `--development` would break that. Each replica allocates ~2.3 GiB with
  `--cache-grid=256MiB` (4.1 GiB with 2 GiB) and has `cpus: 2`, `mem_limit: ${MEM_LIMIT:-4g}`.
- Network `10.203.54.0/24`; actives `.10–.12`, standbys `.13–.14` (`--addresses` takes IPs only).
  Image `ghcr.io/tigerbeetle/tigerbeetle:0.17.9`, client `tigerbeetle==0.17.9`.

## Results

2026-10-03, Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM: 11 CPUs, 24.4 GB, aarch64),
TigerBeetle 0.17.9, one run of each step in this order. Steady load from one client was
~46k transfers/s. No step lost or duplicated an acked transfer, and no request returned an
error. The clients retry on their own while a replica restarts or the view changes.

| step | nodes | acked transfers/s | longest request | notes |
|------|-------|------------------:|----------------:|-------|
| `scale-out` | 3 → 3 + 2 standbys | 35,853 | 680 ms | 5 of 40 s below half the median rate while the actives restarted and the standbys caught up |
| `standby` | 1 active + 2 standbys up | — | no reply in 20 s | committed 1 s after a second active restarted |
| `scale-in` | 3 + 2 → 3 | 47,011 | 308 ms | each active back in the view ~3 s after its restart; restarting the primary moved view 2 → 3 |
| `replace` (replica-1) | 3 | 45,158 | 736 ms | `recover` → in the view after 3 s, state sync of ops 0..54,751 (268 tables) done 4.6 s after start |
| `rolling-restart` to `CACHE_GRID=2GiB` | 3 | 46,371 | 1,861 ms | one request over 1 s, while the primary restarted |

`make benchmark` (1M transfers, 10k accounts, 1 client) before and after the cache resize:

| grid cache | transfers/s | batch p50 ms | batch p99 ms | query p99 ms |
|-----------:|------------:|-------------:|-------------:|-------------:|
| 256 MiB | 343,416 | 15 | 68 | 55 |
| 2 GiB | 221,131 | 18 | 151 | 223 |

A bigger grid cache did not make this benchmark faster. Its 10k accounts and recent
transfers fit in 256 MiB, and the 2 GiB run was slower. That was a single run on a Docker VM
shared with other workloads, so the drop is more likely noise than the cache. The grid cache
pays off when the working set outgrows it, on a dedicated machine ("as large as possible":
RAM − 3 GiB − 1 GiB, `tigerbeetle --help`).
