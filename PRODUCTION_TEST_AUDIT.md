# Recent distributed-example production-test audit

Scope: **2026-10-16 through 2026-11-16** first-added deployment examples: 33
existing directories plus the new minimal Greptime WAL reproducer. Single-node
comparison fixtures are included; older systems and legacy ClickHouse are excluded.
This is a source audit, not a claim that every example was rerun. The per-example
READMEs retain dated execution results and limitations.

## Fresh qualification in this follow-up

- [Neon Kubernetes](postgres/neon/kubernetes-statefulset/): latest shared Go workload
  rebuilt; five core recovery cases and five dependency/backup cases passed.
  Final full body reconciliation: **410,700 ACK rows across 17 journals**, zero
  missing/corrupt/duplicate/partial results. Independent logical restore verified
  **1,000,000 baseline + 406,800 snapshot rows**, with **5,200 post-snapshot ACKs**
  excluded as measured RPO. Failed controller attempts remain failed reports.
  Final strict-fencing follow-ups passed the complete five-case core aggregate
  (133,800 ACK rows) and a separate four-case dependency/restore fixture (114,100
  ACK rows); both reconcile without loss/corruption/partial transactions. The
  restore also rejects writes with native SQLSTATE 25006.
- [Greptime WAL reproducer](greptimedb/wal-bootstrap-reproducer/): failed metadata
  bootstrap lost **100/100 ACK rows** after datanode SIGKILL. Explicit deterministic
  repair restored them. An independent healthy-bootstrap control retained **100/100**
  after the same crash. The loss case deliberately exits nonzero.
- [Greptime operator](greptimedb/kubernetes-operator/): additional forward patch
  upgrade, drain/PDB, partition and bounded pressure cases passed alongside the ten
  existing cases (15 distinct cases, including resumed targets after the failed
  node-address aggregate). Final 627,200 ACK rows across 23 journals reconcile
  without loss/corruption/duplicates; all four failed attempts remain failures.
- [PostgreSQL customer sharding](postgres/customer-sharding/): removed per-case
  truncation and added a separate Go reader, durable attempted/ACK/error journals,
  immutable dataset identities, native hash placement and full body checks on all
  nine physical copies. Fresh million-order baseline, primary crashes on all three
  shards, synchronous-standby pauses on all three shards and a post-recovery
  benchmark passed. **8,030,000 cumulative ACK orders across 11 histories** reconcile
  with zero missing/corrupt/duplicate/misplaced/partial rows. The stronger oracle
  caught broadcast bulk INSERTs: enabled native PgDog rewriting and restarted the
  router; two restricted duplicate-only repairs preserved canonical ACKs and both
  original failed reports. A second fresh-volume five-command lifecycle passed
  another million orders on all nine copies; cleanup left no owned resources.
  See the example for numbers and remaining cases.
- [PgDog pooling](postgres/pgdog-docker-compose-cluster/): independent Go writer/
  reader, preserved attempted/ACK/error histories and full body reconciliation on
  all three physical copies. Million-order baseline plus primary crash, synchronous
  standby pause, asynchronous replica crash, router crash and live pool reload
  passed. **12,000,000 cumulative ACK orders across 12 histories**, zero missing/
  corrupt/duplicate/partial/unresolved rows; native replica write rejection is
  SQLSTATE 25006. Connection bursts validate totals from all historical ACKs.
  Hot reload retained nine connections at configured size four: initial nominal
  cap comparisons are explicitly excluded, then corrected fresh-router/native-cap
  comparisons passed. A fresh five-command lifecycle passed another million
  orders and the burst test; final cleanup left no owned resources or build tags.
  Remaining operational cases are listed below.
- [Citus](postgres/citus-docker-compose-cluster/): preserved order/profile witnesses,
  independent reader, native logical-shard/owner mapping, physical body checks on
  every worker copy and coordinator metadata-copy checks. Primary crashes and
  synchronous-standby pauses on groups 0–3 plus live 3 → 5 → 3 scaling passed:
  **14,318,300 cumulative ACK orders across 12 histories**, 120 ACK profile updates
  across three versions, zero loss/corruption/misplacement/duplicates/partial rows.
  Scale-out used 15 worker copies; scale-in restored nine and proved all six removed
  members empty/stopped after cleanup replay. A same-owner wrong-logical-shard
  negative control produced exactly one placement error, then restored that exact
  ACK body without resetting history. Citus hides physical shards from `pg_class`
  by default; inventory/drain audits now explicitly expose them in their session.
  Fresh-volume lifecycle passed another million orders and 40 witnessed profile
  transactions with final-source nearest-rank percentiles/raw samples; cleanup
  left no owned containers, volumes, networks or build tags.
- [MySQL customer sharding](mysql/customer-sharding/): preserved attempted/ACK/error
  histories, independent Go reader, native Vitess vindex/key-range oracle and full
  body reconciliation through VTGate plus all nine MySQL copies. Million-order
  baseline, primary crashes on all three shards, replica pauses on all three shards,
  one replica crash and VTGate crash passed. **10,252,200 cumulative ACK orders
  across ten histories**, zero loss/corruption/misplacement/duplicates/partial or
  unresolved rows. Native checks verify GTIDs, physical/fixture identities,
  replication source/threads, semi-sync settings and all tablet query services;
  each fault preserves the 14 unrelated service/process identities. Bootstrap
  CLI/seed/socket-permission failures were corrected against the same fixture,
  with their original logs retained. Post-recovery benchmark and retained-state
  setup passed. A second empty-volume five-command lifecycle reconciled another million ACK orders on all nine copies;
  both cleanup passes left zero owned resources/build tags. Remaining extended
  cases are listed below.
- Kafka [Strimzi JBOD](kafka/strimzi-jbod/) and
  [mirroring](kafka/cross-cluster-mirroring/): private kubeconfig export/cleanup
  corrected; evaluated Make recipes prove the exported example-local path. This
  configuration check does not claim a fresh broker/fault lifecycle.

## Controller/workload findings and required backfills

These findings come from the actual execution paths, not just target names.
Closing one fault case does not close the entire deployment matrix.

| Cohort | Inspected source | Remaining concrete gap |
|---|---|---|
| Vitess unsharded, NDB | Example Makefiles, Go `app/main.go`, `scripts/run-fault.sh`, native placement/replica scripts | Fault prerequisites truncate orders between cases. Preserve unique scenario IDs and reconcile older ACK histories; add independent live readers. Exercise each shard/failure domain, while retaining existing native placement checks. |
| MySQL customer sharding | `control.py`, independent `orders-workload/` writer/reader, native vindex/range oracle and all-nine-copy verifier | Core reader/history/placement backfill and eight live fault cases qualified above; post-recovery benchmark, repeated setup, fresh million-order lifecycle and scoped cleanup passed. Online resharding, tablet expansion/shrink, rolling maintenance/upgrades, topology/quorum loss, peer partitions, stale-primary rejection, permanent disk loss, pressure, independent restore and controlled cold/single-record benchmarks remain unrun. |
| Postgres customer sharding | `control.py`, independent `orders-workload/` writer/reader, native hash oracle and all-copy verifier | Core reader/history/placement backfill qualified above. Partitions, DCS/quorum loss, stale-primary rejection, physical disk loss, router outage, rolling upgrades, pressure, backup/restore and online resharding remain unrun. |
| PgDog pooling | `control.py`, independent `orders-workload/` observers and all-copy verifier | Core reader/history backfill and five live role/configuration cases qualified above. DCS/quorum loss, partitions/stale-primary fencing, permanent disk loss, rolling upgrades, pressure and independent restore remain unrun. |
| Citus | `control.py`, native physical-table queries, independent `orders-workload/` observer, monotonic profile witness and `placement_guard.py` | Core reader/history/physical-placement backfill, all group-primary/standby-pause cases and live scale cycle qualified above. DCS/quorum loss, peer partitions, stale-primary rejection, permanent disk loss, rolling upgrades, pressure, independent restore and controlled cold/single-record benchmarks remain unrun. |
| Typesense, Vespa, pgvector, LanceDB | `datasets/esci/client/fault.py`, `workload.py`, engine adapters | Update and search share one churn loop, so blocked writes stop read observation. The prior witness-overwrite path now fails closed before Docker; 16 negative preflight cases passed. Cross-run version histories, independent searches and per-phase success/failure latency remain gaps. Typesense application sharding and LanceDB shared-file clients must not be called native database sharding/replication. |
| SeaweedFS, Garage, RustFS, Ceph | `object-storage/fault.py`, `object-storage/client/workload.py`, native adapters/auditors; Ceph `scripts/fault.py` and `app/recovery.py` | Existing external ACK/byte and physical repair checks are useful. GET follows PUT in one loop: add independent read observation. Restore injected faults on every error; compare survivor start identity as well as container ID. Add role/dependency/quorum and pressure cases beyond data-disk loss. |
| Kafka Strimzi JBOD | Make chaos recipes, C++ producer/Rust consumer | Consumer starts after producer/fault, so continuous read availability and lag are unobserved. Topics reset per case. Add live consumer, durable external delivery/offset history and native ISR/rebuild barriers. |
| Kafka mirroring | Make orchestration, MirrorMaker manifests, producer/consumer | Existing migration and worker/disk cases need preserved cross-phase witnesses, native ISR/offset translation/backlog repair proof and survivor stability. |
| Kafka PostgreSQL enrichment | Make demo, producer/consumer and Go processor | Two-million-message pipeline correctness is not a fault matrix. Add broker, Connect worker/task, processor and PostgreSQL outages with live traffic; quantify duplicate delivery, CDC/cache freshness and backlog. |
| StarRocks operator/Compose, Qdrant TurboQuant | Operator `operations.py`, Compose/Qdrant `fault.py`, independent live observers and reconciler | Existing role/disk/operator cases already have stronger observation paths. Do not duplicate them mechanically. Qualify pinned upgrades, independent restore, pressure and remaining topology/partition cases separately; no fresh rerun in this audit. |
| Neon Compose and Greptime Compose | Local fault/scale controllers and independent observers | Bring the newly qualified Kubernetes dependency cases to supported Compose paths, then extend rolling changes, partitions, pressure and restore. Kubernetes results do not qualify Compose configuration. |
| ClickHouse S3 tier | `scripts/fault.py`, `app/fault_workload.py` | Distinguish remote object durability from local metadata survival; audit independent reads, historical witnesses and native repair per backend before expanding fault counts. |
| Iceberg multi-system | Make cluster fault path and native verifier | Stopping a member then appending/verifying is not continuous read/write observation. Add catalog/object-store/compaction commit interruption and native repair proof. |
| ClickHouse Iceberg | Make archive failure/resume/restart paths and archive oracle | Existing export/verification hooks preserve source data and resume. Add actual dependency outages with concurrent readers; archive atomicity does not establish database HA. |
| Trino Iceberg | `scripts/failover.sh`, heavy-query fixture | Existing worker/storage/catalog cases keep a query active, not a durable writer. Add commit ambiguity and independent reader checks for mutation scenarios. |
| DuckDB ZFS recovery | `app/lab.py`, writers/journals and snapshot replica checks | `spawn` removes same-label journals. Preserve failed evidence and add independent reads. Keep supported single-writer service recovery distinct from unsupported shared-file multi-process writing. |
| ZFS storage lab | `app/lab.py`, fio verify and native pool/send-receive checks | Add independent immutable-data reads during repair. Preserve the existing fenced snapshot-promotion check and quantified 64 MiB unreplicated tail; it is different from synchronous-write durability. Pool reset is fixture setup, not historical ACK preservation. |
| Single-node Neon/Greptime/StarRocks | Lifecycle, verify and benchmark paths | Comparison fixtures, not HA deployments. Qualify cold reopen/full-shutdown persistence separately; cluster results cannot substitute. |

The backfill is **not complete**. Prioritize independent observation and preserved
historical witnesses before adding more fault names. Preserve failed data/evidence;
never reset a failed fixture to make a report pass. Run destructive cases serially.

## Declared-target inventory

The snapshot below is only a triage aid. Aliases can miss tests implemented under
other names or match incomplete controllers; absent recognized targets require
manual applicability review. It is not an execution matrix. Regenerate the raw
inventory with `python3 tools/audit-production-tests.py`; output is ignored under
`tools/results/`, so regeneration cannot overwrite these reviewed findings.

| Example | Shape | Declared categories | No recognized target; review applicability | Evidence status |
|---|---|---|---|---|
| [ceph/s3-cluster](ceph/s3-cluster/) | Compose | crash, disk | suite, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [clickhouse/iceberg](clickhouse/iceberg/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [clickhouse/s3-tiered-storage](clickhouse/s3-tiered-storage/) | Compose | crash, disk | suite, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [duckdb/zfs-recovery](duckdb/zfs-recovery/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [garage/s3-cluster](garage/s3-cluster/) | Compose | crash, disk | suite, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [greptimedb/docker-compose-cluster](greptimedb/docker-compose-cluster/) | Compose | suite, crash, disk, scale-out, scale-in | rolling, partition, pressure, restore, drain | declaration inventory only |
| [greptimedb/kubernetes-operator](greptimedb/kubernetes-operator/) | Kubernetes | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, drain | restore | declaration inventory only |
| [greptimedb/single-node](greptimedb/single-node/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [iceberg/multi-system](iceberg/multi-system/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [iceberg/trino-minio](iceberg/trino-minio/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [kafka/connect-postgres-enrichment](kafka/connect-postgres-enrichment/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [kafka/cross-cluster-mirroring](kafka/cross-cluster-mirroring/) | Kubernetes | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only; private kubeconfig export not found in Makefile |
| [kafka/strimzi-jbod](kafka/strimzi-jbod/) | Kubernetes | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [lancedb/versioned-product-search](lancedb/versioned-product-search/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [mysql/customer-sharding](mysql/customer-sharding/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | eight live fault cases qualified above; other categories require separate work |
| [mysql/ndb-cluster-docker-compose-cluster](mysql/ndb-cluster-docker-compose-cluster/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [mysql/vitess-docker-compose-cluster](mysql/vitess-docker-compose-cluster/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [postgres/citus-docker-compose-cluster](postgres/citus-docker-compose-cluster/) | Compose | suite, crash, scale-out, scale-in | disk, rolling, partition, pressure, restore, drain | ten live operations and placement-negative recovery qualified above; other categories require separate work |
| [postgres/customer-sharding](postgres/customer-sharding/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | six live fault cases qualified above; other categories require separate work |
| [postgres/neon/branching-recovery](postgres/neon/branching-recovery/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [postgres/neon/kubernetes-statefulset](postgres/neon/kubernetes-statefulset/) | Kubernetes | suite, crash, disk, restore | scale-out, scale-in, rolling, partition, pressure, drain | declaration inventory only |
| [postgres/neon/single-node](postgres/neon/single-node/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [postgres/pgdog-docker-compose-cluster](postgres/pgdog-docker-compose-cluster/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | five live role/configuration cases qualified above; other categories require separate work |
| [postgres/pgvector-product-search](postgres/pgvector-product-search/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [qdrant/turboquant](qdrant/turboquant/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [rustfs/s3-cluster](rustfs/s3-cluster/) | Compose | crash, disk | suite, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [seaweedfs/s3-cluster](seaweedfs/s3-cluster/) | Compose | crash, disk | suite, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [starrocks/docker-compose-cluster](starrocks/docker-compose-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [starrocks/kubernetes-operator](starrocks/kubernetes-operator/) | Kubernetes | suite, crash, disk, scale-out, scale-in, rolling, partition | pressure, restore, drain | declaration inventory only |
| [starrocks/single-node](starrocks/single-node/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [typesense/product-search](typesense/product-search/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [vespa/phased-product-search](vespa/phased-product-search/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [zfs/storage-lab](zfs/storage-lab/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [greptimedb/wal-bootstrap-reproducer](greptimedb/wal-bootstrap-reproducer/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
