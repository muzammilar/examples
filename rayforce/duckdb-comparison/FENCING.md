# Journal ownership and filesystem boundaries

Use the five-command lifecycle in [README.md](README.md). Additional cases are
independent and serial:

```sh
make test-lock-filesystem          # standard flock, same/cross-container
make test-journal-writer-lock      # ten native second-open attempts under load
make test-journal-recovery         # two SIGKILL/replay cycles on the volume
make test-production               # capability + the above three cases
```

For the host-share diagnostic, use `make test-lock-filesystem-bind` and
`make test-journal-writer-lock-bind`. They **fail on the measured host**. A failure
is evidence to preserve, not an expected success hidden by the harness.

## A measured storage failure

**2026-11-21**, Apple M4 Pro, native ARM64 Docker runtime. The host-share mount
reported filesystem **`fakeowner`**, while Docker-managed storage reported
**ext4**. These names come from the running processes' mount tables.

| Probe | Host-share bind mount | Docker-managed volume |
|---|---|---|
| Python `flock(LOCK_EX | LOCK_NB)`, another process in same container | Unexpectedly acquires lock | Correctly blocked |
| Same standard-library probe in another container | Unexpectedly acquires lock | Correctly blocked |
| Native Rayforce second journal opener | Unexpectedly opens/replays; **failed** | **10/10 rejected** by journal lock |

The Python probes use independent opens of the same lock file while the first
holder remains live. Reported inode/device values agree. The bind failure therefore
reproduces without Rayforce. This is evidence about **this host-share/runtime**,
not a claim about every Docker bind mount, Mac or network filesystem.

The failed native attempt returned exit **0**, loaded the snapshot and replayed
**804 entries** while the original server was still alive. Its scenario stopped
at that failure; **final ACK/body reconciliation was not run**, so data survival
is unknown for that case. It made no test mutation; concurrent append corruption
was not demonstrated. All failed state/history remains retained in
`results/journal-live/0523e7da97724d3d9b4c3d7e1fae3b16/`.

[FENCING_ENVIRONMENT_RESULTS.json](FENCING_ENVIRONMENT_RESULTS.json) retains that
failure and both final Python controls, including mount tables, source hashes,
image, resource caps, wall/CPU time, RSS and cgroup memory/I/O. Lock files have
zero measured payload/allocated bytes; data compression is inapplicable to this
primitive-only check. The control script exits nonzero if locking is not enforced.

## Current deployment

Live journal scenarios now default to **`JOURNAL_STORAGE=volume`** and use
[docker-compose.journal-volume.yml](docker-compose.journal-volume.yml). Each case
gets a uniquely named/labeled volume. Initialization verifies ownership before
changing that volume's UID/GID; its only added capability is `CHOWN`. Its CPU and
memory counters are retained. The native server and contenders access the exact
same volume; the host witness remains outside it.

Disk observations walk the actual volume from the server container; their
CPU/memory costs are included in server counters. File traversal is non-atomic,
and sampling gaps during stopped/restarted generations are unavailable, not zero.
At the end, writers/readers finish and final native counters are saved. The server
is fenced before `docker cp` exports its state into the case's ignored directory.
Only a successful export permits removal of that case's volume. An export failure
retains the volume and fails cleanup. The terminal stop for export is not an
additional measured recovery cycle. No global Docker prune is used.

`JOURNAL_STORAGE=bind` remains an explicit diagnostic option; do not rely on its
native writer-exclusion guarantee on the measured host. Historical bind-based
single-owner crash/restore results in [JOURNAL.md](JOURNAL.md) retain their original
settings and limitations; they never established safe concurrent writers.

## Live native writer exclusion

Pinned Rayforce development commit
`38fd4be3560d63a5514c1e6883685fe496d45e1a`, synchronous native IPC journal,
`RAYFORCE_NO_FSYNC=0`, **1 CPU / 4 GiB**, swap disabled. Control, writer, reader
and contender applications each have **1 CPU / 512 MiB**. C++ applications use
the upstream IPC API; Python controls failures, witnesses and metrics. Native
contender processes execute a boot script that would exit successfully if the
journal open were wrongly admitted.

| Observation | Result |
|---|---:|
| Native second-open attempts / rejected | 10 / 10 |
| Primary and IPC actor restarts | 0 |
| Seed trade/customer bodies verified | 2,000,000 / 10,000 |
| Live write intents / ACKs / recovered bodies | 1,149 / 1,149 / 1,149 |
| Successful reads / read or write errors | 496 / 0 |
| ACK loss / wrong or duplicate bodies | 0 / 0 |
| Writes / reads during the contender phase | 263 / 113 |
| During-contender write p50 / p99, 263 samples | 0.745 ms / 12.138 ms |
| During-contender read p50 / p99, 113 samples | 10.284 ms / 13.967 ms |

The ten native attempts took **1.035–1.298 ms** each. The contender's loop, including
ten 0.5 s sleeps, lasted **5,023.150 ms**; child processes used **0.009821 CPU
core-s**, with **15.2 MiB peak RSS** and **20.8 MiB cgroup peak**. Its Python wrapper
and whole cgroup consumed **0.095068 CPU core-s**. This is native exclusive-open
rejection, not distributed primary fencing or a cross-host lease test.

Observed server costs were **10.127 CPU core-s**, **538.8 MiB cgroup peak**,
**476.5 MiB PID 1 peak RSS**, **156,155,904 block-write bytes** and **1,845 write
operations**. Charged read counters were **0 bytes / 0 operations** for this cached
run; this does not prove zero physical host reads. The control/writer/reader CPU
costs were **1.322 / 0.130 / 0.086 core-s**, with cgroup peaks
**421.3 / 13.4 / 16.0 MiB**. Counter scopes and initialization costs are in the JSON.

Native files totaled **96,429,640 bytes**. Off-path gzip level 1 produced
**22,956,642 bytes**, **4.201×**, costing **349.483 ms CPU / 351.478 ms wall**.
This is archive compressibility, not native encoded-column compression. The host
controller used **5.330 CPU core-s / 701.7 MiB peak RSS**; Docker CLI/daemon/VM
costs are excluded. Raw state, **70 resource samples**, witness and native logs:
`results/journal-live/7327a5442de9446ba6965e324d0207d3/`.
Compact result: [FENCING_RESULTS.json](FENCING_RESULTS.json).

## Retained-volume crash recovery

The same two-crash/snapshot-tail procedure from [JOURNAL.md](JOURNAL.md) also
passed on the new volume path, with the independent writer and reader running
through both outages. Full body reconciliation verified **2,000,000 seed trades,
10,000 customers and 2,255 live ACKs**, with **0 missing, wrong or duplicate
bodies** and **0 applied unacknowledged intents**. There were **2,765 write
intents / 510 errors**; errors remain separate from acknowledged writes.

| Observation | Cycle 1 | Cycle 2 |
|---|---:|---:|
| First write after start request | 2,145.124 ms | 2,144.042 ms |
| First read after start request | 2,184.205 ms | 2,178.103 ms |
| Observed write success gap, including 5 s hold | 7,386.545 ms | 7,345.150 ms |
| Observed read success gap, including 5 s hold | 7,441.262 ms | 7,409.323 ms |

Observed server totals across generations: **22.449 CPU core-s**, **539.3 MiB
cgroup peak**, **477.3 MiB peak RSS**, **258,703,360 charged block-write bytes**
and **3,457 write operations**. The latter totals take the largest native
checkpoint per generation and are lower bounds on complete lifetime I/O.
Read counters were **0 bytes / 0 operations** under the observed cache state;
device latency/queue depth and physical host I/O remain unavailable. Other role
metrics and all **165 resource samples** are retained in
[JOURNAL_VOLUME_RESULTS.json](JOURNAL_VOLUME_RESULTS.json).

Settled native files were **96,667,465 bytes**; off-path gzip produced
**22,991,226 bytes**, **4.205×**, costing **358.699 ms CPU / 359.139 ms wall**.
The host controller used **9.796 CPU core-s / 569.4 MiB peak RSS**. Different
storage and sampling costs mean these are not performance rankings against the
earlier bind-based run. First-success times include Docker start, replay and
client pacing. Both profiles are bounded correctness runs, not saturation or
sustained-capacity benchmarks. Physical power-loss durability remains unqualified.

Sources: [Linux flock semantics](https://man7.org/linux/man-pages/man2/flock.2.html),
[Docker volume lifecycle](https://docs.docker.com/engine/storage/volumes/),
[pinned native journal lock/recovery](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/journal.c),
[pinned flock wrapper](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/fileio.c).
