# Cold storage checkpoint and restoration

## Current fixture (2026-11-22)

The default current profile now passes `make cold-storage` and three separate
`make cold-readback` invocations. A new owned volume receives the whole archive;
all **140 files** match their hashes and path/type/mode/UID/GID inventory before
native storage opens it. The archive is **1,194,022,727 bytes**. Source allocation
is **1,674,510,336 bytes**; gzip archive size is not Parquet column compression.

| Witnessed table | Acknowledged bodies verified per engine | Restore workflow | Original operational qualification |
|---|---:|---:|---|
| Retained-volume storage crash | 2,400,000 | 34.536 s | Passed |
| Ordered-export memory failure | 3,800,000 | 42.928 s | **Failed; retained as diagnostic** |
| Fresh compactor-output crash | 3,910,000 | 41.689 s | Passed |

Both DuckDB **2.0.1** and ClickHouse **26.10.10.3** reproduce the complete expected
bodies, snapshot identities and hashes: **10.11 million acknowledged rows per
engine** in total. The two ambiguous storage-crash batches remain absent.
The restore uses the pinned SeaweedFS **4.54** image and the current **600 MiB
Go memory limit, 256 MiB reader budget and 16 volume slots**; actual native
settings are checked. The 3.8-million-row diagnostic remains `qualified=false`
and exits with its expected failed-operational status despite passing integrity.

Backup/restore helpers observed **42.057 / 18.417 CPU core-s**, with approximately
**512 MiB** peak charged memory each. They recorded **2,428,452,864 / 0**
read/write block bytes during backup and **2,839,322,624 / 1,319,059,456** during
restore. These sampled Docker-VM counters exclude unobserved edges and do not
measure physical SSD latency or all host-bind writes. Per-table CPU/memory,
disk/I/O, export durations, snapshot-bound compression and provenance are in
[`CURRENT_COLD_RESULTS.json`](CURRENT_COLD_RESULTS.json).

Current commands, after preparing a witnessed recovery table:

```sh
make cold-storage
make cold-readback COLD_CHECKPOINT=results/current/cold-storage/<checkpoint-UUID> COLD_CASE=results/current/<run>/production/<case-UUID> COLD_VERIFICATION=<verification-UUID>
make cold-readback-cleanup COLD_READBACK=results/current/cold-storage/<checkpoint-UUID>/native-readback/<readback-UUID>
make cold-checkpoint-cleanup COLD_CHECKPOINT=results/current/cold-storage/<checkpoint-UUID>
```

Temporary cleanup passed for all three readback projects and the checkpoint
helpers, with **209 host controls**. It removes their fenced containers/private
networks/query-cache volumes and retains the source volume, restored volume,
archive and all witnesses. Current source services and the historical storage
service remained unchanged during replay/cleanup. The earlier outer wrapper's
ignored generated-config comparison and cleanup's initial image-reference
spelling rejection remain recorded; neither changed data or qualified a failed
operational case. Cleanup still requires exact recorded native image/container
identities, even when configuration spells an immutable image as `name@digest`.

This is offline same-host recovery of three witnessed tables. It does not qualify
online backup, host/disk loss or every catalog table. **All-history preservation
and closure for the memory-failed operational case remain unfinished**, so the
main fixture's teardown guard still refuses cleanup.

## Historical fixture

The remaining executed results and commands here belong to the **historical storage
fixture**, including its separate 4.53→4.54 restored-copy upgrade. Before using
them, select that fixture explicitly:

```sh
export LAB_PROFILE=historical DUCKDB_VERSION=2.0.1
```

The main Makefile now defaults to the separate current fixture. Do not point its
results at these historical paths or treat this backup as a checkpoint of the
new current volume; its qualification is recorded separately above.

`make cold-storage` runs with only this example's storage and query services
active. It takes the controller lock, verifies the actual PID 1 and stop signal,
then shuts down the query service **before** storage. Both must exit zero without
OOM or timeout escalation. This is an intentional maintenance outage, not an
online backup or availability test.

The helper mounts the whole original `/data` volume read-only, without a network,
and archives it outside that volume. This includes the filer database, master
metadata, volume data/indexes and mini's private metadata keys. Hashing only the
Parquet files would miss the catalog/control state. The original volume is never
deleted or replaced.

A fresh UUID-labeled volume receives the archive. Before any database process
opens it, the controller compares every file hash and the complete path/type/
mode/UID/GID inventory. Unexpected symbolic links fail the checkpoint. ACLs,
extended attributes and physical host-loss durability are not qualified.
A successful checkpoint retains `native_restore_verified=false`: byte-identical
restoration is a prerequisite for independent database readback, not its substitute.
The original service IDs restart, and bounded health checks must pass.

Both file helpers have **1 CPU / 512 MiB**, a read-only root filesystem and no
network. Reading mode-restricted metadata needs `DAC_OVERRIDE`; only the restore
helper additionally receives `CHOWN` and `FOWNER`. The source mount stays
read-only. CPU, charged memory, disk bytes and device I/O are retained; charged
file cache can reach the limit without an OOM and is not the process heap.

## Executed checkpoint

On 2026-11-22 the reviewed actual Make run passed in **74.373 seconds**, including
**134 host controls**, graceful service shutdown, checkpoint/restore and original
service restart. It restored **146 files**, plus the directory metadata inventory.
The gzip archive is **569,685,468 bytes**. This whole-volume archive size is
separate from the Iceberg tables' encoded Parquet compression ratios.

Measured archive writing took **20.132 seconds**. Source allocation was
**1,004,367,872 bytes**. File-helper CPU through completion was **21.937 core-s
for backup** and **10.087 core-s for restore**; their charged-memory peaks were
about **512 MiB**, including file cache. Docker-VM block counters reported
**287,756,288 read / 0 written bytes** for backup and
**1,666,801,664 read / 834,990,080 written bytes** for restore. These counters do
not establish physical SSD I/O or complete host-bind write attribution; the
archive was written to the host bind despite the backup's zero block-write
counter. The final shell's pipeline-error propagation and positive cleanup were
separately exercised with tiny healthy/failing native helper controls.

The first attempt deliberately remains failed: an overly restricted helper could
not read private metadata. No archive or native restore was qualified by that
attempt. Original services restarted successfully. The next attempt passed;
review then added unconditional sampler cleanup, positive helper fencing and
symlink/no-follow path guards before the final successful Make run above.

Raw evidence is ignored under `results/cold-storage/`. Archives and temporary
restore volumes are retained for the native recovery/upgrade tests; no complete
teardown or automatic deletion of failed evidence is claimed.

## Repeatable native readback

After the checkpoint, run this for each witnessed table that must be recovered:

```sh
make cold-readback COLD_CHECKPOINT=results/cold-storage/<checkpoint-UUID> COLD_CASE=results/<run>/production/<case-UUID> COLD_VERIFICATION=<baseline-verification-UUID>
```

Use a successful full-body verification, or an explicitly diagnostic verification
whose integrity check passed. The case must be registered on the checkpoint's
original volume; its CSVs, configuration and external journals must still match
the baseline. This requires the existing current-version Linux ARM64 runner.
Each invocation creates a new internal, portless Compose project and a fresh
ClickHouse query volume, then opens only the separate restored storage volume.
The original database services may remain running or be stopped; no application
workload may be active in the original example during the replay.

The native verifier independently reconciles every body through both engines,
checks the original snapshot, hashes its saved CSV artifacts and records actual
core/extension/executable versions. A failed original live-test qualification
stays failed in a diagnostic replay, even when the separate restore check passes.
Writers are not started by this read-only audit. Query shutdown completes before
storage shutdown begins. Temporary resources remain stopped for guarded cleanup;
the archive and source/restore volumes are retained.

The actual tracked Make runs passed **146 host controls** and both cases:
**39.427 s** for 2,670,000 acknowledged rows and **42.276 s** for 3,390,000 rows,
each independently checked through both engines. Those workflow times include
control tests, input hashing, startup, body verification and shutdown; they are
not query-only latencies or capacity benchmarks. A native harness negative control
then lost the CLI response after creating a gated verifier. It preserved the
original timeout and failed data verdict while finding and fencing that exact
container; both dependency services still shut down cleanly.

## Native readback and shutdown diagnostic

A separate internal, portless Compose project reopened the restored SeaweedFS
**4.53** volume and used a fresh ClickHouse reader plus actual DuckDB **2.0.1**.
The two retained tables independently reconciled **2,670,000** and **3,390,000**
acknowledged rows per engine, with matching table UUIDs, snapshots and canonical
body hashes. The **1 + 36 ambiguous batches** remained absent. Original journals
and failed live-test verdicts were unchanged. This checks these two witnessed
tables, not every historical table in the catalog.

That diagnostic's overall result remains **failed**: stopping query and storage
concurrently produced a ClickHouse exit **137** after its 30-second stop deadline;
storage exited zero. The complete body checks passed before shutdown. A retained-state rerun
reopened the exact same restored services without resetting data, repeated both
complete checks and passed serial query-before-storage shutdown with **exit 0**
for both services. The earlier concurrent-shutdown verdict remains failed.
Compact counts, resources and provenance are in
[`COLD_STORAGE_RESULTS.json`](COLD_STORAGE_RESULTS.json).

## Guarded temporary-resource cleanup

```sh
make cold-readback-cleanup COLD_READBACK=results/cold-storage/<checkpoint-UUID>/native-readback/<readback-UUID>
make cold-checkpoint-cleanup COLD_CHECKPOINT=results/cold-storage/<checkpoint-UUID>
```

The first target requires passed native readback and removes only its recorded,
positively fenced containers, private network and query-cache volume. The second
removes only the checkpoint's recorded stopped file helpers. Both retain source/
restore volumes, archives and every result. Runtime errors mean unknown state;
healthy listings must prove exact IDs absent. Unknown project resources, changed
mounts/identities or missing final evidence block cleanup. Repeated cleanup is
supported and independently recorded.

Native cleanup removed **17 containers, 4 networks and 4 query-cache volumes**
across the completed baseline/upgrade readbacks and three checkpoint utility
sets. Repeated cleanup passed with no additional removals. The failed helper's
original verdict stayed failed. Older failed diagnostic projects remain preserved;
this does not claim that the whole main lab has been torn down. The final suite
has **171 host controls**; exact removal receipts are in
`COLD_STORAGE_RESULTS.json`.

## Pinned SeaweedFS 4.54 cold upgrade

```sh
make cold-upgrade COLD_CHECKPOINT=results/cold-storage/<checkpoint-UUID> COLD_CASE=results/<run>/production/<case-UUID> COLD_VERIFICATION=<baseline-verification-UUID>
```

This target supports the pinned Linux ARM64 **4.53 → 4.54** transition. It fetches
the public image by digest if necessary, verifies the actual native binary and
reserves only the checkpoint's restored copy for that image **before** opening
its data. Original 4.53 storage and the pre-upgrade archive remain available.
An unknown/newer source image is rejected. After reservation, ordinary
`cold-readback` refuses to use the older source image; use `cold-upgrade` for
further checks on that copy. Rollback means restoring the archive to new storage,
not reopening the upgraded files with 4.53. The native rejection control proved
that refusal created no container or case.

Actual binary: **4.54 / commit 5ef99c1d8**, image digest
`sha256:4b42c212543a0acd4c0e675215ecdcaf62a186ef98def76c7e12701871222d15`.
[Official release](https://github.com/seaweedfs/seaweedfs/releases/tag/4.54).
Both complete-body checks and serial shutdown passed:

| ACK rows per engine | Make workflow (s) | Observed storage CPU (core-s) | Storage lifetime charged-memory peak (MiB) | Encoded Parquet chunk compression |
|---:|---:|---:|---:|---:|
| 2,670,000 | 40.879 | 1.563 | 437.0 | 1.1277× |
| 3,390,000 | 43.460 | 1.071 | 421.2 | 1.5744× |

The storage allocation remained **1,004,400,640 bytes** in both observed windows.
Both engines retained the exact snapshot/body hashes; all **37 ambiguous batches**
remained absent. CPU windows exclude startup/final edges and include observation
probes; memory includes file cache. The query/verifier budgets and CPU/memory/disk/
I/O results are in [`SEAWEED_UPGRADE_RESULTS.json`](SEAWEED_UPGRADE_RESULTS.json).
These are cold compatibility checks, with the same **1 CPU / 1 GiB** storage cap,
default reader budget and four volume slots. They do not qualify a rolling/live
upgrade, prove a fix for the historical OOM or establish capacity/performance.

## Failed-case preservation

```sh
make archive-compound-case FAILED_CASE=results/compactor-20261122/witnessed/production/2b463a19-1a8c-43bf-b84d-5a0fa03f5836
make verify-history
make down
```

This deliberately narrow migration handles the identified aborted-reader/storage-
OOM case. It rehashes **333 case artifacts**, **54 external backup/readback
artifacts** and the original source seals, verifies native catalog heads and
positively fences all **17 registered processes**. The receipt retains
`passed=false`, `status=failed-but-preserved`; it does not invent the missing
completed compactor proof or relax qualification for other failed experiments.
The independent restored-copy proof covers **3,390,000 acknowledged rows per
engine**, with all **36 ambiguous batches absent**.

The first publication attempt failed safely on standalone helpers that inherited
Compose image ownership labels. After exact helper ownership checks and removal,
a native control verified the corrected helper clears those labels without
changing main-project resources. The failed attempt remains recorded.

Actual Make runs passed **193 host controls**: archive publication **59.432 s**,
history verification **74.882 s**, and application `down` **37.921 s**. These are
workflow durations, not query or capacity benchmarks. Application resources were
removed; the original storage and query volumes, archive, restored copy and all
raw evidence remain. Full `docker-down` has **not run**: automatic approval review
rejected deletion of the original volumes and image tags, pending explicit
approval. Compact receipts are in
[`COMPOUND_PRESERVATION_RESULTS.json`](COMPOUND_PRESERVATION_RESULTS.json).

Next: finish authorized fixture retirement, then qualify fresh 4.54 bounded-memory/
capacity settings and continuous compactor recovery. The original operational
failure remains failed.


## Fresh 4.54 settings probe

A separate **1 CPU / 1 GiB**, network-isolated, tmpfs-only container verified the
proposed settings before selecting them for a new live fixture. Native metrics
reported `go_gc_gomemlimit_bytes=629145600` (**600 MiB**), and the master's
`/dir/status` reported **16 volume slots**. The pinned binary was 4.54 /
`5ef99c1d8`. Its explicit process arguments included a **256 MiB reader budget**
and disabled S3 data-cache capacity. There is no independently verified runtime
getter for that reader budget; process arguments establish configuration only.

The startup scrape observed **0.15 process CPU seconds** and **153,411,584 bytes
RSS**. These are single observations, not run totals, peak memory or benchmark
results. The probe used capped tmpfs, mounted no source database volume, exited
zero without OOM and was removed. No workload, sustained pressure, volume
expansion or compactor fault ran. The candidate Compose override remains
unselected by the lifecycle. See
[`STORAGE_SETTINGS_RESULTS.json`](STORAGE_SETTINGS_RESULTS.json).

Pinned upstream source documents [mini configuration precedence](https://github.com/seaweedfs/seaweedfs/blob/4.54/weed/command/mini.go)
and [native metric registration](https://github.com/seaweedfs/seaweedfs/blob/4.54/weed/stats/metrics.go).
Explicit flags take precedence over saved mini options; later live tests must
also check effective settings and behavior on their actual fixture.
