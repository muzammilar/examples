# Recovery boundaries and investigation record

Never rerun the seed over an acknowledged fixture. The controller preserves the
fixture identity, source receipts and external fsynced intent/ACK histories. Each
new case first fences source mutations and reconciles all earlier histories,
including failed attempts. A later pass does not change an earlier failed status.

## Native recovery cases

- `make connector-lifecycle`: pause the connector, prove native connector/task
  state, keep source writers and historical/recent readers live, resume and wait
  for a newly sampled acknowledged insert. Reconcile all eleven fields and EOF
  on each fixed ClickHouse replica, then run the native Airflow reconciliation DAG.
- `make backfill-recovery`: add the planned shipping field, correct 1,000 orders,
  request a native incremental snapshot through Airflow, observe new incremental
  records and interrupt Connect while the snapshot is still incomplete. Retain
  its offsets, schema history, signal identity and all source/target data. Require
  native completion, no repeated initial snapshot, exact final rows and the
  reconciliation DAG. Replaying the same request must leave one logical signal.
- `make worker-recovery`: SIGKILL one TaskManager, hold it down while source ACKs
  continue, restart the same container/storage and require every job vertex
  RUNNING plus a newer completed checkpoint and newly visible sampled ACK.

These are independent targets, also run serially by `make test`. Do not insert a
reset between cases. Stop actors before a final fenced reconciliation. `make down`
requires that verification before dropping application tables/jobs; `make docker-down`
then removes only this Compose project's infrastructure and locally built images.

## Failures found while implementing the lab

The ignored `results/` directory retains failed case statuses, task logs, source
histories and corrective-run evidence. Curated reports must include those failures.

1. Upstream images declared anonymous data/config volumes. A strict ownership
   guard rejected them before seeding. Source tables and receipts were proved
   empty; mounts were converted to explicitly named example-owned volumes. Only
   recorded, now-unreferenced old volumes were removed. This was infrastructure
   recovery, not a test of database data-loss recovery.
2. A retract-mode Debezium Kafka sink rejected an upsert-only SQL clause. The
   native SQL CLI printed an error but exited zero. Submission now checks both
   output errors and the actual running job. `make resume-submission` can resume
   this specific pre-job failure only after proving the unique original seed ACK,
   its native atomic receipt/counts, and the absence of another submitted job.
3. ClickHouse alias substitution made a `max(offset) AS offset` diagnostic query
   invalid when another aggregate referenced `offset`. The diagnostic alias is
   now `last_offset`; stored application data was not reset.
4. Repeated source fencing tried to revoke already-absent grants. Fencing now
   verifies the exact three-grant state and handles an already fenced source;
   partial permission drift fails instead of being silently accepted.
5. Persisted CDC row coordinates can contain a GTID set preceding their current
   transaction. Comparing them directly with the latest global GTID set stalled
   on an idle source. A later documented native `log` signal establishes the
   complete prior cut. The recorded coordinates and output barriers remain the
   basis for the final comparison; a sleep alone is not a fence.
6. The Airflow 3.4 task-instance schema has `dag_id` and `run_id`, not the assumed
   `dag_run_id` column. The first live connector run recovered and its native DAG
   succeeded, but its controller failed collecting task evidence. That attempt
   stays failed; the corrected controller reran the case on retained data.
7. The idempotent signal insert uses `ON DUPLICATE KEY UPDATE`, which requires
   UPDATE permission even on its first insert. Airflow's request task failed
   because the initial signal-table grant only included SELECT/INSERT. The
   source schema now grants UPDATE on that table, and the incremental-progress
   gate also checks failed DAG state immediately. Existing failed DAGs are kept.

8. The interrupted incremental snapshot and its Airflow DAG completed, but the
   final history scan exceeded a 1 GiB bytes-read guard. The bounded allowance is
   now 8 GiB for accumulated CDC history; resident query memory remains capped at
   1 GiB. The failed controller attempt stays failed and the affected case is rerun.

## Scope limits and future work

This focused level-3 lab does not qualify MySQL primary failover, loss of the
JobManager/checkpoint volume, permanent Kafka/Keeper/ClickHouse disk loss, network
partitions, whole-stack restart, upgrades, or independent backup/restore. Those
need separate declared faults and preserved-history checks. The single physical
Docker host cannot prove host/AZ fault tolerance. Airflow's local-only authentication
and static lab credentials are not a production security deployment.

The shipping field is a planned compatible addition, not general schema evolution.
Kafka partition expansion, changed key encoding and primary-key changes are not
qualified. Raw CDC and tombstones are intentionally retained: logical deletion is
not physical erasure. Replaying a stable signal is checked at the source row and
native completion boundary; it is not a universal exactly-once orchestration claim.
