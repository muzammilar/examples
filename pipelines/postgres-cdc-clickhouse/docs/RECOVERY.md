# Snapshot and retained-worker recovery

This example distinguishes source durability, connector progress and target
convergence. A healthy Connect REST endpoint alone proves none of them.

## Read the failure before retrying

Keep `results/fixture.json`, `results/cases/`, Kafka internal topics, the PostgreSQL
slot/publication and every volume. The fixture stage prevents blindly rerunning
seed or snapshot tests over acknowledged state. Source transaction errors remain
ambiguous until matched against their receipt and the external intent/ACK log.

Inspect `make status`, Connect task status/logs, PostgreSQL replication-slot state,
the source offsets, Kafka partition ends and the target's raw topic/partition/offset
coverage. Verify the current tables on **each fixed replica**, not a load-balanced
endpoint. `make verify` reconciles existing witnesses; it does not create a new
passing chaos case or erase a failed test.

The expected crash procedure is: prove the owned Connect process died, observe
its slot inactive while source transactions continue, restart that same worker,
observe its task and retained slot resume, stop/fence source mutations, check all
partition positions and native consumer commits, then reconcile complete rows,
deletions, revisions, customer joins and every acknowledged immutable insert.
Do not drop the slot or delete offsets as a recovery shortcut.

## Findings from the first fixture

- With broker topic auto-creation disabled, omitting
  `__debezium-heartbeat.cdc` stalled the snapshot-to-streaming transition with
  `UNKNOWN_TOPIC_OR_PARTITION`. Creating that owned topic at RF3/minISR2 allowed
  the **retained** connector to proceed. The normal setup now creates it explicitly.
- The initial reader incorrectly assumed that an incomplete snapshot range was
  already contiguous. Kafka partitions can deliver separated rows before all
  chunks arrive. It reported ID 990506 while expecting missing ID 990504. The
  returned row's body was correct; this was an oracle defect. The corrected reader
  validates each returned ID/body, bounds and strict order, allowing gaps until
  catch-up. Separate unit controls reject wrong values and duplicate IDs.
- That run remains failed. Retained verification subsequently matched **1,806
  external ACKs** and **1,001,187 active rows**, including all eight fields, on each
  of three replicas, with the same SHA-256 row-stream hash. Only then was its
  application state removed and its infrastructure destroyed for a fresh run.
- A disk-accounting attempt used UID 0 in a capability-dropped ClickHouse
  container. UID 0 could not traverse the data owner's private directories;
  the controller correctly failed before teardown. Accounting now runs under the
  service's configured identity. No data permissions or capabilities were weakened.
- The previously recorded Debezium image digest no longer resolved from the
  registry despite being cached locally. Clean setup now pins the verified
  3.7.1.Final index shown in Compose. A cached image is not a clean-pull proof.

## Boundaries

One host, one PostgreSQL primary, one Connect worker and shared Docker storage
remain correlated failure domains. Surviving a connector process crash does not
qualify primary failover, WAL retention exhaustion, disk loss, arbitrary schema
changes, backup restore or physical power loss. Repeated same-key mutations in one
source transaction need a stronger ordering/version contract than this fixture.
Those are future cases, not passed tests. This is a focused level-3 run rather
than an exhaustive performance matrix.
