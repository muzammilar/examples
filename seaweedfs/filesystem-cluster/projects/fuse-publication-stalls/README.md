# SeaweedFS FUSE publication stalls

This focused diagnostic shares the parent filesystem lab's infrastructure,
application build, state and cleanup. It localizes the approximately one-second
publication stalls preserved in the [HTTP/FUSE comparison](../native-http/).
It does not repeat or replace that comparison, including its failed large trial.

## Quick start

Run from `seaweedfs/filesystem-cluster/`:

1. `make docker-up` — build the instrumented C++ workload and prepare owned storage.
2. `make up` — create this lab's independent writer/reader mounts.
3. `make test` — establish exact baseline correctness and three native copies.
4. `make down` — reconcile every retained publication and remove the applications.
5. `make docker-down` — remove only this lab's infrastructure and state.

Between steps 3 and 4 run `python3 scripts/observe.py publication-stalls`, then
`make resources`. The stage probe is a separate test, not a hidden fault inside
the baseline. It publishes **128 × 256 KiB** files under a new UUID, records each
successful native syscall stage, then checks both mounts and all three volume
copies. Its UUID is registered before writing, so a failure remains part of later
reconciliation; do not remove the fixture or reseed it.

## Files and measurements

- `diagnose.py`: bounded controller and stage summary, using the parent native oracle.
- `../../files-workload/main.cpp`: native C++17 workload, CMake build and per-stage
  `steady_clock` measurements; Dockerfile and entry points remain parent-owned.
- `reports/`: curated outcomes; ignored parent `results/publication-stalls-*`
  holds original journals, output and independent replica checks.

Stages are exclusive create/open, body write, file fsync, close, rename, directory
open, directory fsync and close. Optional Ceph `syncfs` is a separate recorded
stage when that tracked source is materialized by its existing project. This
SeaweedFS probe leaves the publication procedure, durability settings and payloads
unchanged. Timing/JSON bookkeeping adds overhead; summed stage time excludes that
bookkeeping while total operation time includes it.

A slow stage identifies where the caller blocks, not which internal mount,
kernel, network, filer or volume mechanism caused it. Preserve that distinction.
Resource sampling includes the two mount services and all backend dependencies;
filesystem allocation is not a measured compression ratio.

## Results — 2026-11-27

[Curated diagnosis and all-role resource measurements](reports/DIAGNOSIS-2026-11-27.json).
All **128 probe publications** succeeded. Together with the baseline, both mounts
and the native oracle verified **256 ACK files / 67,108,864 logical bytes**,
**768 direct replica reads / 201,326,592 physical body bytes**. No wrong body,
missing ACK or ambiguous publication was observed. Scoped teardown passed.

| Stage | p50, ms | p95, ms | Maximum, ms | Calls over 500 ms |
|---|---:|---:|---:|---:|
| File open | 1.560 | 5.146 | 6.109 | 0 |
| Body write | 0.102 | 0.223 | 1.042 | 0 |
| File fsync | 5.357 | 11.494 | 15.334 | 0 |
| File close | 0.033 | 0.058 | 0.315 | 0 |
| Rename | 6.088 | 1,019.055 | 1,059.331 | 28 |
| Directory fsync | 0.023 | 0.050 | 0.127 | 0 |

The long publications block in **rename**, rather than file fsync. Pinned
[rename source](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/mount/weedfs_rename.go)
acquires DLM locks and invokes the filer. The pinned
[lock client](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/cluster/lock_client.go)
sleeps one second after errors, contention or redirects. That is a plausible
candidate, **not a traced cause**: this run did not capture per-lock RPC events.
Disabling DLM changes cross-mount coordination guarantees and is not an equivalent
fix. Exact lock/metadata/cache attribution remains future work.

| Resource scope | Observed value |
|---|---|
| Sampled whole probe + verification | 7.954 core-seconds; 1,740,640,256-byte aggregate memory peak |
| Sampled Docker block activity | 69,632 read bytes / 170,958,848 write bytes |
| Native cgroup snapshot delta | 368,640 read bytes / five reads; 171,024,384 write bytes / 1,442 writes; excludes shell-less etcd |
| Application native resources | 6,892-KiB peak RSS; 0.095 user+system CPU seconds, excluding mount/backend processes |
| Owned volumes after probe | 586,833,920 allocated bytes, including RF3 and metadata |
| Identical-body gzip level 1 reference | 33,554,432 → 33,566,976 bytes, ratio 0.999626×; 441.928 CPU ms / 448.704 wall ms |

The compression reference ran separately inside the reader container after the
stage probe, with identical independently generated bodies. It measures
compressibility, not native SeaweedFS compression. Native counters include this
supplement and verification; the Docker probe window excludes the supplement.
The report keeps scopes separate. Device I/O latency/queue depth, native etcd I/O
counts and native codec ratios remain unavailable/unmeasured. There was no cache
eviction, saturation sweep or new competitive comparison.
