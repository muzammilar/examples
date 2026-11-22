# Cold storage checkpoint and restoration

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

Next steps are verified cleanup of the owned temporary resources and a separately
pinned newer-release upgrade using the preserved checkpoint. An in-place
downgrade is not assumed safe.
