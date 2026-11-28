# Publication and maintenance recovery

These are bounded local failure cases, not a production HA qualification. Each
named million-message phase runs once per fixture. Retry a failed read/recovery
against its preserved state; do not replay an ambiguous producer or reset volumes.
After a completed sweep, export/tear down and create a new fixture to repeat it.

## Catalog publication

`make publication-failure` requires the healthy baseline. Its independent reader
keeps reading a previously verified Parquet body from the old pinned snapshot;
the producer writes another million messages without controller pauses.

1. Hold `FOR UPDATE` on the exact JDBC catalog row. The holder has a bounded
   lifetime and writes its PID/metadata pointer to an fsynced witness.
2. Require a Flink `UPDATE` waiting on that holder in `pg_stat_activity`, and map
   its client address to an owned TaskManager. Kill that process, not an idle role.
3. Keep source ACKs/read observations running, release the lock and restart the
   same retained TaskManager. Require the same job ID and native checkpoint restore.
4. Verify every row/partition against all Kafka ACKs, snapshot ancestry and the
   full old snapshot through both independent Arrow and native Spark readers.

The executed fault blocked checkpoint 48's **three files / 8,000 records**.
Its snapshot, created before the worker crash, remains in the final ancestry.
PostgreSQL may finish an in-flight request after the caller dies: killing a client
does not establish that the mutation failed. Final reconciliation found exactly
two million logical keys; this is evidence for this tested recovery, not a
universal exactly-once claim across arbitrary connectors or transactions.

A native checkpoint can complete before the sink's catalog publication. The
source's checkpoint/ACK counters alone therefore do not establish query visibility.
Iceberg's snapshot timestamp is allocated before commit; it is not a visibility
timestamp. Use observed catalog pointers and complete independent readback.

## Compaction

`make compaction-failure` follows successful publication recovery. It starts a
third million-message phase and calls native Spark `rewrite_data_files` with
bin packing, a 128 MiB target and one concurrent file-group rewrite. It discovers
an active Spark executor through that application's REST API, maps its address
to an owned worker, kills/restarts the worker and requires a failed native task
plus a nonempty replacement receipt. Native Spark recomputes lost work.

The executed procedure replaced **119 files with 9**, rewriting **9,300,641
bytes** after one `ExecutorLostFailure`. Concurrent appends remained present;
the final three-million-row table and old two-million-row snapshot both passed.
The procedure's `failed_data_files_count=0` describes its recovered final result;
it does not erase the failed task.

Retained snapshots still need old files. Rewriting files is not physical
reclamation. After draining the writer, `make down` inventories committed Parquet
objects against the conservative union of retained manifests. Unreferenced objects
are **candidates**, not proof that deletion is safe. Incomplete multipart uploads,
unlisted temporary state and retention expiry need separate tests. No destructive
orphan removal or snapshot expiration runs here.

## Setup failures retained

- Initial resource preflight assumed Compose memory limits were integers; this
  Compose version returned strings. The failure happened before service creation.
- Calling `sql-client.sh` directly bypassed the image entrypoint's
  `FLINK_PROPERTIES` rendering. Submission hit a connection refusal and its
  180-second deadline; native REST proved there was no submitted job or producer
  witness. The corrected path calls the native entrypoint. Setup CPU rose from
  0.125 to 0.5, with the revised 10.875-CPU total recorded between phases.
- Python `dict(snapshot.summary)` conflicted with this pinned PyIceberg model's
  Mapping/Pydantic behavior. The adapter now uses its native JSON serializer.
  Failed reader invocations remain recorded; the million-message source was not
  reloaded. Subsequent complete Arrow and Spark readbacks passed.

Do not attribute these adapter/setup mistakes to incorrect database answers.

## Further work

Catalog/JobManager/driver HA, catalog loss and independent restore, S3 outage,
permanent Kafka/Seaweed disk loss, quorum/partition failures, mixed data types,
upserts/deletes, retention expiry and multipart reclamation are unrun here.
All simulated replicas share one VM/disk; this cannot establish AZ or power-loss
durability. A production deployment needs independent failure domains, supported
manager/catalog HA, authentication/TLS, capacity headroom, observability and backups.
