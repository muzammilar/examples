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
- [Vitess unsharded](mysql/vitess-docker-compose-cluster/): retained ACK histories,
  independent primary reader and full body checks through VTGate and on all three
  MySQL copies. Million-order baseline, primary crash, replica pause, replica crash
  and VTGate crash passed: **6,184,600 cumulative ACK orders across six histories**,
  zero loss/corruption/duplicates/partial or unresolved rows. Native GTID, source/
  thread, query-service and semi-sync barriers passed; all eight unrelated service
  identities were preserved per fault. An incomplete initial bootstrap witness was
  rejected before order allocation, preserved and extended only after full native
  seed/ownership checks. Post-recovery benchmark and retained-state setup passed.
  A fresh-volume five-command lifecycle recorded all three identities from
  creation and verified
  another million ACK orders on all copies; both cleanups left zero owned
  resources/build tags. Extended cases remain below.
- [NDB](mysql/ndb-cluster-docker-compose-cluster/): preserved order/profile
  histories, independent partition reader and full bodies through both SQL
  frontends. Native NDB scan FRAGMENT values verify order/customer placement;
  native inventories verify three live replicas per partition on all nine data
  nodes. Initial baselines and all eight live data/SQL/management/router faults
  passed:
  **11,721,100 cumulative ACK orders across 12 histories**, plus one million
  profiles and **6,000 ACK quota mutations across three versions**, zero missing/
  corrupt/misplaced/duplicate/partial/unresolved bodies. All 13 unrelated process
  identities were preserved per fault. Post-recovery benchmark, retained-state
  setup and fresh five-command lifecycle
  passed. Fresh-volume checks verified another million ACK orders, one million
  profile bodies and 2,000 quota ACKs; both cleanups left zero owned resources/
  build tags. The old correlated disk-full ACK loss remains documented. Failed bootstrap/controller
  attempts and the interrupted seed remain evidence; none reset application data.
- [SeaweedFS](seaweedfs/s3-cluster/): separate Compose writer/reader, exclusive
  append-only attempt/outcome journals, preserved cross-run oracle and native
  nonempty-volume / elected-master / elected-etcd target discovery. Seven live
  fault runs covered volume crash, fenced empty-volume replacement, both control
  leaders, filer, gateway and a second crash of the rebuilt member. **5,991 ACK
  objects across eight retained histories** reconcile without loss/corruption;
  the pre-injection controller failure remains failed. Four ambiguous PUTs yielded
  one exact optional object and three absent objects; the gateway case recorded
  one HTTP 503 read error / 10.123 s completion gap. All 21 unrelated container/
  main-process identities were preserved per run. Post-recovery benchmark completed
  41,536 requests without failure; full reconciliation checked **36,893 objects**
  and 110,694 direct replica chunk reads. Current-revision metadata agreement,
  full-stack cold reopen and repeated setup passed. Fresh-volume lifecycle plus
  two further owner-selected crash/disk runs retained another 2,117 ACKs with no
  errors/loss; both cleanup passes left zero owned resources/build tags. Fresh
  verification exposed native `Volumes: null` on an empty server; both SeaweedFS
  auditors were fixed and tested without resetting the fixture. The failed log
  remains. A subsequent default-path lifecycle fix passed two complete fresh
  cycles with native replica bytes/metadata checks: 10,133 objects / 30,417 direct
  chunk reads per cycle, 26 controls, preserved retired witnesses and zero owned
  resources after each cleanup. All ten unrelated anonymous volumes stayed
  unchanged. This lifecycle-only follow-up did not rerun the live fault/benchmark
  matrix. Extended cases remain explicitly unrun below.
- [Garage](garage/s3-cluster/): separate Compose writer/reader, preserved fsynced
  attempt/outcome histories and exact native ring/block-byte checks. Controller-managed
  process crash, empty owned data disk, router crash and re-crash of the rebuilt
  member passed. **13,817 ACK objects across eight retained histories** reconcile
  without loss/corruption; the failed initial repair check and two manually assisted
  investigations retain their labels. Final S3 readback checked **44,718 objects**,
  and the native audit checked **139,248 required copies on all 256 partitions**.
  All nine unrelated process identities stayed unchanged per qualified fault.
  Fixed-prefix repair runs with both traffic roles live; metadata sweeps are
  explicit, recorded recovery assists. The router is a measured availability
  boundary (442 read errors, 30.478 s successful-read gap). Full cold restart
  exposed missing peer-discovery configuration; applying native bootstrap peers
  recovered the retained fixture without reset, then corrected cold reopen and
  retained setup passed. A fresh-seed full-copy gap (239 missing third copies)
  exposed the need for an explicit startup repair barrier; the failed fixture was
  recovered without PUT replay. Corrected fresh startup and 29 controls passed.
  The final-configuration three-case live aggregate then retained another
  **3,035 ACK objects across three histories**, zero lost/corrupt/misplaced bodies
  and 39,990 required native copies verified. Its process/router read gaps were
  15.136 s / 30.473 s; the independent reader recorded one / 449 errors. Final
  41,536-request benchmark passed with zero failures, with no missing required
  native copies before post-benchmark repair. Final readback checked
  **33,936 objects / 2,002,817,024 bytes** and **106,902 required native copies**;
  whole-stack retained reopen passed in 57.673 s. Both corrected fresh-volume
  lifecycles passed; the final fixture checked another 10,133 objects / 30,885
  required native copies. Retired manifests, failed histories and teardown proofs
  survive recreation, and the archived dataset passes the offline report. All
  four cleanups left zero owned resources/build tags. Extended cases remain
  explicitly unrun below.
- [RustFS](rustfs/s3-cluster/): independent Compose writer/reader, preserved
  fsynced attempt/ACK histories, native EC2+2 membership and canonical deep-scan
  checks, four metadata paths per required key and retained out-of-line payload
  fingerprints. The larger fixture retained **15,910 ACK objects across eight
  histories**; two failed disk cases were diagnosed/recovered without application
  PUT replay and remain failed reports. Post-benchmark readback verified
  **46,811 objects / 2,424,705,024 bytes**, 187,244 metadata paths and 3,584 payload
  fingerprints with zero reconciliation errors. Mount admission required a direct
  named-volume subpath mount; set-format completion with zero object outcomes is
  distinct from mandatory bucket deep-scan coverage. Both corrected fresh-volume
  lifecycles passed with preserved archived witness/status hashes. The final-source
  complete four-case production aggregate passed in **1,035.312 s**, retaining
  **6,227 ACK objects across four histories** and verifying 16,360 complete bodies,
  65,440 metadata paths and 512 payload fingerprints without loss/corruption.
  Corrected whole-stack reopen compared unchanged pre-stop fingerprints in
  **86.971 s**; one-off workload admission was negatively tested. All 35 controls
  and six offline proof probes passed. The warm 41,536-request benchmark had zero
  failures (1 MiB / 16 clients: 177.52 PUT / 673.80 GET MiB/s). All three cleanups
  left zero owned resources/build tags. Inline decoding remains native; metadata
  filenames alone do not prove independent inline bytes. Extended cases remain
  explicitly unrun below.
- Kafka [Strimzi JBOD](kafka/strimzi-jbod/) and
  [mirroring](kafka/cross-cluster-mirroring/): private kubeconfig export/cleanup
  corrected; evaluated Make recipes prove the exported example-local path. This
  configuration check does not claim a fresh broker/fault lifecycle.

- [Ceph RGW](ceph/s3-cluster/): independent Compose writer/reader, fsynced
  attempt/outcome histories and native guest/17-daemon survivor witnesses.
  Separate whole-guest crash, serial-fenced empty-disk replacement, rebuilt-member
  crash and single-router SIGKILL passed: **10,319 ACKs / 10,823 attempts across
  four histories**, zero lost/corrupt/stale objects. Full readback after the warm
  benchmark verified **13,583 objects / 855,937,024 bytes**, including three exact
  optional ambiguous outcomes. All **321 PGs** completed fresh deep scrubs with
  three copies on three distinct logical guest hosts. Proxy loss caused 501 GET
  errors and a 30.349 s successful-read gap. The new 4,384-request benchmark had
  zero failures; 1 MiB / 16 clients measured 19.11 PUT / 40.91 GET MiB/s under
  QEMU TCG, excluded from native-container rankings. Whole-stack cold reopen
  passed in 347.914 s with three new kernel boot IDs and unchanged cluster/OSD
  UUIDs. Retained-state setup/report and direct SDK reads through all three RGWs
  passed. Twenty-seven controls include stale benchmark-proof rejection. A fresh
  default five-command lifecycle, native/body verification and report passed:
  **1,069 objects / 106,860,544 bytes**, 321 fresh PG scrubs and unchanged archived
  witness/status hashes. All 39 recorded code/configuration hashes stayed unchanged.
  Both cleanups left zero owned resources/build tags and all ten unrelated volume
  identities unchanged. Initial PG autoscaling required explicit scrub rescheduling;
  the check kept full coverage rather than treating `HEALTH_OK` as proof. Extended
  cases remain unrun below.

- **Typesense:** independent Go writer/reader and cross-run history qualified on
  the same 100,000-product corpus. Four three-node role/disk cases at read lag 10
  passed; aggregate verification retained **187,010 ACKs / 218,900 attempted
  updates** across seven runs. The earlier default-readiness short-search failure
  and aborted journal-ordering harness run stay failed. Every local copy's full
  metadata and all float32 coordinates matched: **300,000 documents / 115.2M
  coordinates**. Whole-stack reopen failed its Compose probe budget after
  **383.55 s**; later no-replay reconciliation verified all original volumes,
  images and containers. Final proof finished 619.78 s after captured before-state,
  including diagnosis/wait/readback, not isolated RTO. Native snapshot interval
  3,600 s plus frequent process restarts caused full growing-log replay.
  Retained setup/test/report passed. A fresh default lifecycle passed, with
  **158 archived evidence hashes unchanged**, zero owned resources/tags and all
  ten unrelated volume identities unchanged. Warm dense search, eight clients:
  **1,085.56 QPS / 12.24 ms p99 / 0.9879 recall**; all 6,000 timed requests passed.
  The nine-node application-managed sharded profile passed all **12 core cases**:
  **88,496 ACK / 5,944 ambiguous updates**, zero whole-operation read failures,
  one stale GET and correct placement/full vectors on all nine copies. An extra
  different-member failure kept rebuilt ts3 online, with **405 affected-shard
  ACKs while ts2 was absent**. Final 13-case report reconciled **95,659 ACKs /
  102,090 attempted updates**. Its 8,000-request warm benchmark had zero failures:
  global dense eight-client **359.11 QPS / 37.76 ms p99 / 0.9957 recall**;
  customer dense **440.97 QPS / 30.14 ms p99 / sampled recall 1.0**. Budgets differ
  from the three-node profile; these are not normalized scaling rankings.
  Sixteen controller controls, four routing controls and Go race tests/vet passed.
  Fresh sharded lifecycle and cleanup passed with **296 archived evidence hashes
  unchanged**, zero owned resources/tags and the same ten unrelated volumes.
  The updated single-node lifecycle, correctness, retained reopen (**106.85 s**),
  benchmark (6,000 requests / zero failures) and cleanup also passed: dense
  eight-client **794.44 QPS / 42.39 ms p99 / 0.989 recall**. Extended matrix
  items below remain unrun; API copy proof is not independent RocksDB decoding.

## Controller/workload findings and required backfills

These findings come from the actual execution paths, not just target names.
Closing one fault case does not close the entire deployment matrix.

| Cohort | Inspected source | Remaining concrete gap |
|---|---|---|
| NDB | `control.py`, independent `orders-workload/`, mutable quota witnesses and official native scan tool | Order/profile history, independent reads, native fragment placement and eight live role cases qualified above; post-recovery benchmark, retained-state setup, fresh million-order/profile lifecycle and scoped cleanup passed. Native SQL readback is not independent physical-replica byte verification. Expansion/reorganization, partitions/quorum, physical disk loss, pressure, upgrades and independent restore remain unrun. |
| Vitess unsharded | `control.py`, independent `orders-workload/` observer and three-copy body oracle | Core history/reader backfill and four live role fault cases qualified above; post-recovery benchmark, retained-state setup, fresh million-order lifecycle and scoped cleanup passed. Resharding/tablet changes, planned/rolling maintenance/upgrades, resource changes, topology/quorum loss, partitions/stale-primary rejection, permanent disk loss, pressure, independent restore and controlled cold/single-record benchmarks remain unrun. |
| MySQL customer sharding | `control.py`, independent `orders-workload/` writer/reader, native vindex/range oracle and all-nine-copy verifier | Core reader/history/placement backfill and eight live fault cases qualified above; post-recovery benchmark, repeated setup, fresh million-order lifecycle and scoped cleanup passed. Online resharding, tablet expansion/shrink, rolling maintenance/upgrades, topology/quorum loss, peer partitions, stale-primary rejection, permanent disk loss, pressure, independent restore and controlled cold/single-record benchmarks remain unrun. |
| Postgres customer sharding | `control.py`, independent `orders-workload/` writer/reader, native hash oracle and all-copy verifier | Core reader/history/placement backfill qualified above. Partitions, DCS/quorum loss, stale-primary rejection, physical disk loss, router outage, rolling upgrades, pressure, backup/restore and online resharding remain unrun. |
| PgDog pooling | `control.py`, independent `orders-workload/` observers and all-copy verifier | Core reader/history backfill and five live role/configuration cases qualified above. DCS/quorum loss, partitions/stale-primary fencing, permanent disk loss, rolling upgrades, pressure and independent restore remain unrun. |
| Citus | `control.py`, native physical-table queries, independent `orders-workload/` observer, monotonic profile witness and `placement_guard.py` | Core reader/history/physical-placement backfill, all group-primary/standby-pause cases and live scale cycle qualified above. DCS/quorum loss, peer partitions, stale-primary rejection, permanent disk loss, rolling upgrades, pressure, independent restore and controlled cold/single-record benchmarks remain unrun. |
| Typesense | Example-owned controller, independent Go inventory writer/reader, retained witnesses, direct local metadata/vector proof | Three-node core role/disk cases, nine-node 12-case suite and an extra post-rebuild peer-loss case qualified above. Fresh sharded archival/lifecycle cleanup passed, preserving 296 evidence hashes. Single-node lifecycle, reopen, correctness and benchmark also passed. Whole-stack reopen exceeded its probe budget; later same-volume persistence verified. Partitions/minority behavior, membership scaling, bounded pressure/I/O errors, upgrades, independent restore and snapshot-cadence experiments remain unrun. |
| Vespa | Example-owned `control.py`, independent Go inventory writer/reader, preserved histories and full native metadata/vector auditor | Backfill in progress on 2026-11-18: fresh 100,000-product load, retained-history correctness and full 300,000-copy / 115.2M-coordinate checks passed. Native recovery completed in a controller-crash attempt whose reporter failed; its failure remains recorded. Empty-volume replacement and a second-host failure with a directed fresh-ACK read from the rebuilt member passed; all 26 unrelated native process identities stayed unchanged in the latter. Read-only routing retries, optional native metrics and latency-free diagnostic counters now have explicit handling/controls. Whole-stack retained reopen passed with 9,134 new ACKs; eight histories reconcile 77,225 ACK updates / 89,200 attempts, including all failed-case histories. The warm 8,000-request benchmark had zero failures; teardown preserved and hash-verified 526 files. Eleven host controls include rejection of benchmarking after an incomplete newest case. A fresh five-command lifecycle plus non-controller-host crash passed: 32,157 ACKs / 35,870 attempts across three passing histories, zero acknowledged loss, all final copies agreeing and all 28 unrelated native processes unchanged during the crash. Its final correctness case added 6,860 ACKs with zero failed/stale/wrong reads. Another 191 files were archived; cleanup left zero owned resources/tags and preserved all ten recorded foreign volumes. The complete serial target, repeated retained setup, single/sharded reruns and extended failure matrix remain unrun. |
| pgvector, LanceDB | `datasets/esci/client/fault.py`, `workload.py`, engine adapters | Update and search share one churn loop, so blocked writes stop read observation. The prior witness-overwrite path now fails closed before Docker; 16 negative preflight cases passed. Cross-run version histories, independent searches and per-phase success/failure latency remain gaps. LanceDB shared-file clients must not be called native database sharding/replication. |
| SeaweedFS | Example-owned `control.py`, independent `traffic.py`, preserved journals, native placement/bytes and current-revision metadata audit | Reader/history/survivor backfill, seven live faults, rebuilt-member restart, benchmark, cold reopen and fresh lifecycle/two more live faults qualified above. Scaling/drain, peer partitions, quorum loss, bounded pressure, upgrades, metadata disk destruction, physical EIO/power loss and independent restore remain unrun. |
| Garage | Example-owned `control.py`, independent `traffic.py`, preserved histories and native ring/block auditor | Core reader/history/survivor backfill and four controller-managed fault cases qualified above. Peer partitions, native quorum loss, topology/resource changes, rolling upgrades, metadata-disk loss, pressure, physical EIO/power loss and independent restore remain unrun. |
| RustFS | Example-owned `control.py`, independent `traffic.py`, preserved histories, native canonical heal and read-only slot/fingerprint auditor | Core reader/history/survivor backfill, full four-case fresh fault aggregate, benchmark, corrected cold reopen and two fresh lifecycles qualified above. Peer partitions, quorum loss, pool/resource changes, upgrades/rollback, pressure, physical EIO/bitrot/power loss and independent restore remain unrun. |
| Ceph | Example-owned `control.py`, independent `traffic.py`, preserved SDK histories and `native/` PG/deep-scrub/process auditor | All four core faults and warm benchmark qualified with independent live readers, preserved histories, native OSD UUID/serial fencing and all 17 daemon process witnesses. Cold reopen, retained-state setup, fresh default lifecycle, archived witness integrity and scoped cleanup also qualified above. Isolated daemon faults, quorum/partitions, pressure, topology changes, upgrades and independent restore remain unrun. |
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
| [ceph/s3-cluster](ceph/s3-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | four live faults, benchmark, cold reopen and fresh lifecycle/cleanup qualified above; extended categories remain unrun |
| [clickhouse/iceberg](clickhouse/iceberg/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [clickhouse/s3-tiered-storage](clickhouse/s3-tiered-storage/) | Compose | crash, disk | suite, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [duckdb/zfs-recovery](duckdb/zfs-recovery/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [garage/s3-cluster](garage/s3-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | live history/reader/native-copy recovery qualified above; extended categories require separate work |
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
| [mysql/ndb-cluster-docker-compose-cluster](mysql/ndb-cluster-docker-compose-cluster/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | eight live role cases qualified above; extended categories require separate work |
| [mysql/vitess-docker-compose-cluster](mysql/vitess-docker-compose-cluster/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | four live fault cases qualified above; extended categories require separate work |
| [postgres/citus-docker-compose-cluster](postgres/citus-docker-compose-cluster/) | Compose | suite, crash, scale-out, scale-in | disk, rolling, partition, pressure, restore, drain | ten live operations and placement-negative recovery qualified above; other categories require separate work |
| [postgres/customer-sharding](postgres/customer-sharding/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | six live fault cases qualified above; other categories require separate work |
| [postgres/neon/branching-recovery](postgres/neon/branching-recovery/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [postgres/neon/kubernetes-statefulset](postgres/neon/kubernetes-statefulset/) | Kubernetes | suite, crash, disk, restore | scale-out, scale-in, rolling, partition, pressure, drain | declaration inventory only |
| [postgres/neon/single-node](postgres/neon/single-node/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [postgres/pgdog-docker-compose-cluster](postgres/pgdog-docker-compose-cluster/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | five live role/configuration cases qualified above; other categories require separate work |
| [postgres/pgvector-product-search](postgres/pgvector-product-search/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [qdrant/turboquant](qdrant/turboquant/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [rustfs/s3-cluster](rustfs/s3-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | full fresh live fault aggregate, corrected cold reopen and two fresh lifecycles qualified above; extended categories remain unrun |
| [seaweedfs/s3-cluster](seaweedfs/s3-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | live faults, cold reopen and fresh lifecycle qualified above; extended categories remain unrun |
| [starrocks/docker-compose-cluster](starrocks/docker-compose-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [starrocks/kubernetes-operator](starrocks/kubernetes-operator/) | Kubernetes | suite, crash, disk, scale-out, scale-in, rolling, partition | pressure, restore, drain | declaration inventory only |
| [starrocks/single-node](starrocks/single-node/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [typesense/product-search](typesense/product-search/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [vespa/phased-product-search](vespa/phased-product-search/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [zfs/storage-lab](zfs/storage-lab/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [greptimedb/wal-bootstrap-reproducer](greptimedb/wal-bootstrap-reproducer/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
