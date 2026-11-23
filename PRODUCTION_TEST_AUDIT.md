# Recent distributed-example production-test audit

Scope: **2026-10-16 through 2026-11-22** first-added deployment examples: 35
existing directories plus the minimal Greptime WAL reproducer (36 Makefiles).
Single-node comparison fixtures are included; older systems and legacy ClickHouse are excluded.
This is a source audit, not a claim that every example was rerun. The per-example
READMEs retain dated execution results and limitations.

The [expanded test concepts](AGENTS.md#evaluation-stages-and-backend-contract) and
[mandatory resource/storage measurements](AGENTS.md#mandatory-measurements-for-every-experiment)
also apply to this cohort. Earlier correctness/recovery passes do not establish
coverage for newly listed cases or complete CPU, memory, compression and disk
measurements. Audit these separately and mark gaps; this document adds no new
execution evidence.

## Fresh qualification in this follow-up

- [DuckDB/ZFS](duckdb/zfs-recovery/reports/FULL_SUITE_RESULTS.json): fresh five-command
  lifecycle passed on 2026-11-22. Continuous guest-crash recovery verifies
  **2,000,000 seed + 323,000 acknowledged rows** and 180 independent read results,
  zero missing/wrong/partial/duplicate bodies. The complete disk/crash/replica/
  archive sequence verifies **7,453,000 bodies across exported copies**, including
  all **30,000 source acknowledgements** absent on the asynchronous standby.
  Native ARC remains 256 MiB across reboot. Nineteen host controls, native CSV/
  timeout-fencing controls, all-history replay and scoped teardown pass; CPU,
  memory, compression, disk bytes and virtual-device IOPS are recorded. Earlier
  ARC drift and verifier failures keep their failed verdicts. Pinned Quack source
  overrides the requested HTTP timeout with 86,400 seconds; the observed 75.058 s
  ambiguous operation is not a qualified three-second deadline. Full production,
  physical host-loss and comparative performance qualification remain false.
- [CedarDB/PostgreSQL mutable workload](cedardb/backup-recovery/projects/transactional-mutations/docs/MUTABLE.md):
  newer million-row healthy/SIGKILL qualification verifies **2,972 ACKed
  transactions**, zero acknowledged loss or wrong/partial rows, full seed/body/
  receipt/history reconciliation, native controls and 23 host controls. Native
  per-device IOPS now accompany byte counters. The failed prototype is externally
  archived before verified fixture replacement; its failed verdict remains intact.
  The [12-cell sustained comparison](cedardb/backup-recovery/projects/transactional-mutations/docs/MUTABLE_BENCHMARK.md)
  independently verifies 108,440 ACKs and 83,243 complete query results with zero
  loss/corruption. CedarDB throughput is 3.29×/3.03× alone/live; PostgreSQL uses
  less charged memory. Warm-query/automatic-statistics limits and observer costs
  remain explicit; current 26 controller tests pass. Earlier evidence follows:
  2026-11-22 checkpoint, four 10,000-row healthy/container-SIGKILL cases verify
  **3,036 ACKed paired UPDATE/DELETE-reinsert transactions**, zero acknowledged
  loss or wrong/partial rows. Native read-only/rollback controls, snapshot readers,
  complete receipt/body replay, 19 host controls and legacy regressions pass.
  Four earlier short benchmark-harness cells pass. Preserve the PostgreSQL CASE-typing failure and invalidated
  weak prototype; resource/storage evidence is separate from a performance claim.
- [Rayforce native AOF segments](rayforce/duckdb-comparison/projects/rayforce-aof/docs/AOF_SEGMENTS.md):
  2026-11-22, 159 native files; complete 10,000/10,003-event rotation/reopen/backup
  verification and known uncommitted-tail recovery. Full scan rejects one sealed
  CRC edit while native open checks only the tail. Deliberate final-file deletion
  loses **46/49 ACK bodies**, detected by the external witness before new writes;
  intact backups restore every body. All resource/storage and native timing costs
  are recorded. Expanded 12-case production workflow completes with both existing
  strict payload/AOF integrity failures still nonzero. This expected-loss offline
  diagnostic does not qualify live concurrent scans, interrupted rotation or
  physical disk recovery.
- [DuckDB native WAL recovery](rayforce/duckdb-comparison/projects/duckdb-recovery/docs/DUCKDB_RECOVERY.md):
  2026-11-22, two retained-volume SIGKILLs with independent C++ Quack writer/reader
  sessions and checkpoint under load. Final strict-phase run verifies **2,000,000
  seed trades, 10,000 customers and 2,364 live ACK bodies**, zero loss/corruption.
  TLS wrong-token/wrong-pin controls, 33 host controls, six language/engine
  correctness cases and 18 negative controls pass; five-command cleanup passes.
  Full eleven-case production workflow completes with the two known Rayforce
  payload/AOF integrity failures still nonzero. The final DuckDB rerun strengthens
  healthy/outage assertions and temperature-separated reporting; it is a separate
  recorded run. Resource/storage costs and gaps are explicit; no HA, device-loss,
  capacity or comparative recovery-speed claim.
- [Kafka enrichment](kafka/connect-postgres-enrichment/reports/VERIFIED_RESULTS.json): on
  2026-11-21, fresh broker, active Connect worker, Go processor and PostgreSQL
  SIGKILL cases passed with **8,020,480 ACKs**, live independent source/output/SQL
  observations and full historical body/partition reconciliation. Three healthy
  minimum-count diagnostic trials passed with another **6,013,312 ACKs**.
  Final replay covers **18,038,912 ACKs across nine data histories**, zero
  acknowledged loss/corruption. The original interrupted broker case received a
  separate clean data reconciliation and remains failed; the RF1 setup failure
  and known stale-CDC negative control remain preserved failures. Results include
  CPU/memory/block-byte/storage samples and CRC-verified native compression.
  These are short pipeline observations, not sustained/cold-cache capacity tests.
  [Lifecycle qualification](kafka/connect-postgres-enrichment/reports/LIFECYCLE_RESULTS.json)
  now covers a separate 2,001,024-ACK retained-state fixture and a final empty-fixture
  five-command run with 1,001,024 ACKs, zero loss/corruption and scoped cleanup.
  All five final commands passed without retries; 23 controller tests pass. Four
  prototype lifecycle failures retain their verdicts. Extended failure domains
  and sustained/cold/fair comparisons remain unqualified.
- 2026-11-21 archive rechecks: PostgreSQL/pgvector **2,321 file hashes / 16 host
  controls**, Strimzi **1,535 hashes / 11 controls**, MirrorMaker **1,054 hashes /
  15 controls** passed. Their native faults were not rerun for these checks.
  Compact results were committed before user-requested bulky export/journal
  cleanup. Earlier preservation statements describe the original qualification;
  consult current READMEs for retention limits. Deleted raw data cannot be replayed
  from summaries/hashes.
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
- Kafka [Strimzi JBOD](kafka/strimzi-jbod/): independent live C++/Rust actors,
  fsynced intent/ACK/offset witnesses and retained histories; native role discovery,
  positive CRI process fencing, ISR/directory-lag barriers and repaired-broker
  preferred-leader full replay. The serial million-ACK correctness/broker/controller/
  write-denied-directory aggregate passed with a 300-second delivery budget:
  **4,004,096 new ACKs**, zero ACK loss/corruption. Three fresh workload-process
  trials checked **6,004,096 ACKs**, median **33,352.06 ACKs/s**; bounded warm-client
  diagnostics, not Kafka capacity or a competitor ranking. A 10-second-budget
  disk test remains **FAILED**: 10,427 extra physical copies among ambiguous sends,
  zero lost/corrupt/duplicated acknowledged orders. All 593,266 failed-topic binary
  records agree with independent Java/Rust fingerprints; later tests compare its
  exact failed physical state, never relabel the original run as passing. Three
  earlier harness failures remain recorded. Retained setup preserved all six
  Kafka processes and both directories, followed by full history readback. Full
  cleanup/hash-verified evidence: 1,535 files under ignored
  `kafka/strimzi-jbod/results/archives/45ac3389ca4f4b4bb9822a096685bf3d/`.
  Extended operator/failure cases and an isolated short-timeout reproducer remain.
- Kafka [mirroring](kafka/cross-cluster-mirroring/): fresh kind cutover validated
  2,000,000 source orders, their mirrored copy and 2,000,000 new west orders.
  The Rust/native-formatter checkpoint barrier rejected three premature snapshots,
  then proved exact source/checkpoint/target commits, idle groups, RF=3/ISR=3 and
  zero visible target replay on all six partitions (48.203 s across four attempts).
  A final-source second fixture passed the same 2,000,000 + 2,000,000-order
  workflow (first-attempt checkpoint gate: 9.613 s); no performance comparison is
  inferred from these differently warmed waits. Body/key validation and explicit
  validated-offset commits were tightened.
  This cutover qualification is separate from the live fault cases below.
  Additional live baseline, actual MM2 source-worker SIGKILL and retained-volume
  west-leader SIGKILL passed with **6,003,072 cumulative source ACK orders**.
  Independent source/target readers, host-fsynced intents/outcomes and complete
  body/partition replay found zero ACK loss/corruption/duplicate logical records.
  Recovered west broker served all six new-topic partitions for replay. The
  original empty setup failure remains failed with explicit zero-write evidence.
  The original empty-storage run remains **failed** after exit 139/Docker crash.
  Separate post-reboot reconciliation verified 8,004,096 ACKs without loss; a
  failed source task required the supported failed-task-only Connect restart.
  A fresh empty-storage case passed with 2,001,024 new ACKs and full replay of
  **10,005,120 cumulative ACKs** on each cluster, zero loss/corruption. Fencing,
  distinct volume identity, deleted old storage, RF3 native rebuild and service
  through the rebuilt leader were proved. Fault→rebuilt-copy service: 72.070 s.
  CRI accounting measured 713.214 observed CPU core-seconds over 505.476 s,
  including full historical replay; it is not a capacity benchmark. Witness-backed
  teardown and recreated-lab lifecycle passed, with 774 + 280 files independently
  hash-verified and six unrelated containers preserved. Fresh cutover checked
  4,000,000 unique orders; exact checkpoints passed on attempt 3 after 39.125 s.
  Separate setup retained 15 managed Pod/container identities, followed by fresh
  source/two-batch-west readbacks. Excluded-topic, overlapping-retry and initial
  one-run retained-verifier failures remain recorded with separately reconciled
  source data. Topic preflight, inherited cutover locking and two-run verification
  now guard those paths. No physical-disk qualification is claimed.


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
| Vespa | Example-owned `control.py`, independent Go inventory writer/reader, preserved histories and native full metadata/vector auditor | Core follow-up verified 2026-11-18. The initial flat run reconciles 77,225 ACKs / 89,200 attempts across eight histories; three harness failures remain preserved (routing export, optional metrics, latency-free diagnostic reporting). A fresh flat lifecycle added 32,157 ACKs / 35,870 attempts. Single-node clean/SIGKILL reopen and retained setup passed six histories: 37,308 ACKs / 44,280 attempts. The sharded full serial matrix/retained setup passed ten histories: 139,501 ACKs / 149,030 attempts, including active-control-host loss, content failures across all three replica groups, empty content3 volume replacement, directed fresh-ACK read from rebuilt content3 while content0 stayed fenced, and mixed graceful/forced whole-stack reopen. Latest final proofs detect zero acknowledged loss and validate complete bodies/all 384 coordinates; sharded audits perform 900,000 directed gets with 100,000 IDs per group. This is native API readback, not independent disk decoding. Seventeen host controls and ten native-copy controls cover profile isolation, unknown fencing state, optional metrics, counter resets and wrong rebuilt-shard bodies. Warm benchmarks have 8,000 requests/zero failures per profile; single/sharded native CPU costs are 29.52/85.20 core-seconds over whole benchmark commands, with differing budgets/durability/recall explicitly stated. Cleanup hash-verified 526/191/323/887 files across the four fixture archives, removed only owned resources/tags and preserved all ten recorded foreign volumes. Detailed dated counts, latencies and limits are in the example README. The exact single-node aggregate and scoped teardown subsequently passed on 2026-11-19: five histories, 35,144 ACKs / 43,530 attempts, 8,386 ambiguous attempts, zero acknowledged-version loss and 258 hash-verified archived files; all 23 pre-existing volumes were preserved. The flat full aggregate/retained setup, partitions/quorum loss, scaling, upgrades, pressure/EIO, independent restore and fair multi-trial/cold comparisons remain unrun. |
| pgvector | Example-owned `control.py`, independent Go inventory actors and native full-copy verifier | Core backfill verified 2026-11-19. Fresh cluster eight-case suite passed primary/synchronous-standby crash, safely fenced empty-volume clone, direct rebuilt-copy proof with another peer absent, and graceful/SIGKILL whole-stack reopen: 27,870 ACK / 33,860 attempts, 5,990 ambiguous, zero acknowledged-version loss/corruption/retries. Single profile passed five histories: 8,720 ACK / 12,900 attempts, 4,180 ambiguous. All histories are fsynced and preserved; native timeline/replay barriers are reached while writers remain live, followed by all-body/bit-exact vector proofs on all copies (115.2 million coordinates per cluster proof). Earlier 28 cluster histories retain four failed harness cases; fixes were followed by fresh tests. Six warm 1,000-query diagnostics per profile have no query failures, with all-server sampled CPU/memory; these are not cold/multi-trial capacity comparisons or integrated CPU/peak RSS. Sixteen host controls, seven native groups and three Go race-tested controls pass. Cleanup hash-verified 1,414/342/565 files and preserved all 23 pre-existing volumes. Partitions/quorum, native scaling, upgrades, pressure/EIO, independent restore and fair cold/multi-trial comparisons remain unrun. |
| LanceDB | Example-owned serial controller, independent Go actors, fsynced cross-case histories and native SDK-view proofs | Core backfill verified 2026-11-19: eight-case cluster aggregate plus stronger live maintenance passed, 16,760 ACK / 24,250 attempts / 7,490 ambiguous; single seven-case aggregate plus final maintenance passed, 10,540 ACK / 17,230 attempts / 6,690 ambiguous. Both have zero acknowledged-version loss/corruption, complete metadata/vector proofs on all views and ten visible witnessed non-ACK versions, kept ambiguous. Cluster native compaction reduced 795 to ten fragments and 7,930 to 80 unindexed rows. These workers share one durable catalog; they are not independent replicas/shards. Each profile's final six-case benchmark validates complete bodies/versions on 6,000 timed requests and 63,651 returned hits, with zero failures and 17/22 resource samples. The separate pre-maintenance ID-only diagnostic exposed fragmentation cost; timing boundaries differ, so no controlled speedup/winner is claimed. Retained cluster setup and a second fresh single lifecycle/retained setup passed another 2,270 ACKs. Cleanup hash-verified 625/493/74 files and preserved all 23 pre-existing volumes. Seven host, nine native SDK and six Go race-tested control groups pass. Network/volume-I/O faults, pressure, SDK upgrades, independent restore and fair cold/multi-trial capacity comparisons remain unrun; native replica/quorum repair is unsupported by this embedded shared-catalog deployment. |
| SeaweedFS | Example-owned `control.py`, independent `traffic.py`, preserved journals, native placement/bytes and current-revision metadata audit | Reader/history/survivor backfill, seven live faults, rebuilt-member restart, benchmark, cold reopen and fresh lifecycle/two more live faults qualified above. Scaling/drain, peer partitions, quorum loss, bounded pressure, upgrades, metadata disk destruction, physical EIO/power loss and independent restore remain unrun. |
| Garage | Example-owned `control.py`, independent `traffic.py`, preserved histories and native ring/block auditor | Core reader/history/survivor backfill and four controller-managed fault cases qualified above. Peer partitions, native quorum loss, topology/resource changes, rolling upgrades, metadata-disk loss, pressure, physical EIO/power loss and independent restore remain unrun. |
| RustFS | Example-owned `control.py`, independent `traffic.py`, preserved histories, native canonical heal and read-only slot/fingerprint auditor | Core reader/history/survivor backfill, full four-case fresh fault aggregate, benchmark, corrected cold reopen and two fresh lifecycles qualified above. Peer partitions, quorum loss, pool/resource changes, upgrades/rollback, pressure, physical EIO/bitrot/power loss and independent restore remain unrun. |
| Ceph | Example-owned `control.py`, independent `traffic.py`, preserved SDK histories and `native/` PG/deep-scrub/process auditor | All four core faults and warm benchmark qualified with independent live readers, preserved histories, native OSD UUID/serial fencing and all 17 daemon process witnesses. Cold reopen, retained-state setup, fresh default lifecycle, archived witness integrity and scoped cleanup also qualified above. Isolated daemon faults, quorum/partitions, pressure, topology changes, upgrades and independent restore remain unrun. |
| Kafka Strimzi JBOD | Independent live C++/Rust actors, external histories, native CRI/ISR/directory proof and repaired-leader replay | Core and three fresh-process benchmark trials qualified above; short-deadline ambiguity/duplicate run remains failed with exact retained state. Still add scaling/vertical changes, operator reconciliation/outage, rolling/node/drain/PDB, partitions/quorum, upgrades, pressure, persistent PVC replacement and independent restore; isolate the duplicate reproducer. |
| Kafka mirroring | Make orchestration, native checkpoint formatter and Rust offset/ISR/EOF inspection | Cutover/checkpoint proof plus live baseline, source-worker crash and retained-volume west-leader crash passed with preserved ACK histories and full replay. Fresh empty-storage replacement and separate post-crash full reconciliation passed; the original interrupted run stays failed. Recreated lifecycle/retained readback and cleanup passed; extended operator/failure cases remain pending. Historical Pod deletions do not qualify them. |
| Kafka PostgreSQL enrichment | Example-owned witness-backed Compose harness, independent C++/Rust actors and SQL probes | Four fresh core crash cases and three minimum-count diagnostics passed 2026-11-21; final replay checks 18,038,912 ACKs across nine histories with zero loss/corruption. Original RF1 setup, stale-CDC and interrupted broker failures retain their verdicts. Native compression and resource/storage results are committed in the example. Fresh five-command lifecycle (1,001,024 ACKs) and separate retained-state qualification (2,001,024 ACKs) passed with clean scoped teardown; extended fault domains and sustained/cold/fair comparisons remain unqualified. |
| StarRocks operator/Compose, Qdrant TurboQuant | Operator `operations.py`, Compose/Qdrant `fault.py`, independent live observers and reconciler | Existing role/disk/operator cases already have stronger observation paths. Do not duplicate them mechanically. Qualify pinned upgrades, independent restore, pressure and remaining topology/partition cases separately; no fresh rerun in this audit. |
| Neon Compose and Greptime Compose | Local fault/scale controllers and independent observers | Bring the newly qualified Kubernetes dependency cases to supported Compose paths, then extend rolling changes, partitions, pressure and restore. Kubernetes results do not qualify Compose configuration. |
| ClickHouse S3 tier | `scripts/fault.py`, `app/fault_workload.py` | Distinguish remote object durability from local metadata survival; audit independent reads, historical witnesses and native repair per backend before expanding fault counts. |
| Iceberg multi-system | Make cluster fault path and native verifier | Stopping a member then appending/verifying is not continuous read/write observation. Add catalog/object-store/compaction commit interruption and native repair proof. |
| ClickHouse Iceberg | Make archive failure/resume/restart paths and archive oracle | Existing export/verification hooks preserve source data and resume. Add actual dependency outages with concurrent readers; archive atomicity does not establish database HA. |
| Trino Iceberg | `scripts/failover.sh`, heavy-query fixture | Existing worker/storage/catalog cases keep a query active, not a durable writer. Add commit ambiguity and independent reader checks for mutation scenarios. |
| DuckDB ZFS recovery | `app/lab.py`, continuous `app/live.py`, durable histories, native export/fencing and guarded teardown | Fresh full lifecycle now preserves old datasets/journals and verifies continuous crash recovery plus all historical bodies before cleanup. Independent readers, persistent ARC and quantified asynchronous tail checks pass. Quack's ignored requested timeout remains an explicit deadline gap; deeper production coverage is incomplete. This is a single writable server with remote clients, not shared-file multi-writer DuckDB. |
| ZFS storage lab | `app/lab.py`, independent immutable-body reader, native fio/send-receive checks and guarded external pool archives | Default 4 MiB/s five-command lifecycle passed 2026-11-22: 157 mirror/140 RAIDZ2 complete 64 MiB SHA reads, including each native replacement interval; both 512 MiB payloads verified with clean scrubs. Snapshot promotion preserves required bytes and quantifies the missing 64 MiB asynchronous tail. Thirty-five controls and scoped teardown pass; CPU/memory/compression/disk-byte/IOPS evidence is published. Earlier explicit-rate checkpoint and archive-checker/Python-prerequisite failures remain separate. Native archive restore, per-flush external ACKs, continuous replication observers, guest-reboot ARC persistence, pressure and wider failure combinations remain unqualified. |
| Single-node Neon/Greptime/StarRocks | Lifecycle, verify and benchmark paths | Comparison fixtures, not HA deployments. Qualify cold reopen/full-shutdown persistence separately; cluster results cannot substitute. |

The backfill is **not complete**. Prioritize independent observation and preserved
historical witnesses before adding more fault names. Preserve failed data/evidence;
never reset a failed fixture to make a report pass. Run destructive cases serially.

## Current testing pass and next work

**Current scope (2026-11-23):** experiments are paused. The completed results and
known failures remain evidence; remaining cases are deferred in each system and
example README under **Future work / TODO**, and indexed in
[the backlog](TODO.md#deferred-follow-ups-for-covered-systems). Continue breadth-first
exploration when requested rather than exhausting one system's optional matrix.
GreptimeDB versus Victoria remains an accepted focused comparison, with equivalent
contracts/resources and a user-selected performance depth. Correctness gates
apply to the selected configuration, not every unrelated optional backfill.

**Local-only scope:** use isolated Docker/Compose and lab-only credentials. Do not
inspect/load host AWS configuration or credentials, or access real cloud resources.
Managed Tigris execution is deferred; use the separately named local SeaweedFS S3
reference for DuckDB/Iceberg tests. Its observations cannot qualify Tigris.

The 2026-11-22 source inventory covers all **36 Makefiles in this recent cohort**;
it is not a completed upstream/code review or a rerun of all systems. The detailed
findings above remain the execution/gap record. The separate
[Rust embedded survey](rust/EMBEDDED_DATABASE_SURVEY.md) records the initial candidate
research. The supplemental [Rust embedded project](rust/embedded-databases/)
is outside that original deployment inventory: its executed native-process
qualification is recorded separately below.

| Work group | Present boundary | Next testing pass |
|---|---|---|
| Fjall/redb embedded Rust | 100k and million-pair native-process crash/reopen and fresh-copy restore pass; million-pair proofs verify 1,000,789/1,000,778 ACKed pairs with zero loss or wrong/partial data | Keep one applied redb ambiguous intent unacknowledged. These are shared-host functional observations, not a comparative performance winner. Scoped teardown and clean fixture recreation pass. Defer deeper KV tests during breadth-first exploration; select further runs explicitly. |
| CedarDB/PostgreSQL | Million-row mutation correctness/recovery and twelve sustained cells pass | Explicitly prepared statistics/plans, ingestion/cache variants and bounded storage pressure remain. Keep warm-query, automatic-statistics and single-node limits visible. |
| Rayforce/DuckDB | Native DuckDB WAL recovery passes; Rayforce strict integrity cases remain failed | Keep failed cases visible. Diagnose/retest integrity before new qualified performance rankings; timing failed cases is diagnostic only. |
| DuckDB/ZFS | Fresh five-command lifecycle and full disk/crash/replica/archive sequence pass, including 2.323 million crash-case bodies and 7.453 million checks across exported copies | Preserve the earlier ARC drift/verifier failures separately. All 30,000 asynchronous tail ACKs remain on the fenced source; they are absent on the promoted snapshot as declared RPO. Quack source overrides the requested timeout, so qualify phase-specific transport/deadline behavior next; retain the wider production/pressure/restore and fair-performance gaps. |
| ZFS storage | Default 4 MiB/s five-command lifecycle and full mirror/RAIDZ2/replication suite pass; independent readers cover every native replacement interval | Preserve the earlier explicit-rate checkpoint and failed archive-checker/Python-prerequisite attempts separately. Native stream checks do not establish restore, fio growth is not a per-flush ACK witness, and exported-source promotion is administrative fencing. Defer deeper storage tests during breadth-first exploration; select further runs explicitly. |
| Iceberg/archive examples | Native data/shard checks and archive gates exist; continuous mutation/reader and commit-ambiguity gaps remain | Add external histories and continuous observation through catalog/object-store/compaction interruptions, then verify the committed snapshot and retained source before cleanup. Do not infer HA from queries on surviving copies. |
| Recent relational, search and S3 clusters | Core per-example proofs above pass on their recorded configurations; extended matrices and new holistic measurement requirements are incomplete | Revisit applicable quorum/partition, scaling, maintenance/upgrade, pressure and independent-restore cases across the cohort. Preserve version-specific qualification and every earlier failed attempt. |
| GreptimeDB/Victoria | Metrics/log baselines and guarded Greptime recovery have results; normalized complete-stack comparison is unqualified | Verify equivalent contracts/budgets and stable configurations first, then follow the single/batch/native-ingestion and cold/warm-query matrix in the [observability plan](observability/README.md#greptimedb-vs-victoria-planned-comparison). Do not extrapolate tracing support or a whole-stack winner. |

This queue implements the [progressive passes](AGENTS.md#progressive-testing-across-systems):
survey breadth first, establish missing correctness/recovery evidence, then deepen
tests and compare stable configurations. No new live coverage is established by
the target inventory alone. Isolate comparative measurements from other heavy jobs;
parallel functional labs follow the resource and ownership rules in AGENTS.md.

## Declared-target inventory

The snapshot below is only a triage aid. Aliases can miss tests implemented under
other names or match incomplete controllers; absent recognized targets require
manual applicability review. It is not an execution matrix. Regenerate the raw
inventory with `python3 tools/audit-production-tests.py`; output is ignored under
`tools/results/`, so regeneration cannot overwrite these reviewed findings.

| Example | Shape | Declared categories | No recognized target; review applicability | Evidence status |
|---|---|---|---|---|
| [cedardb/backup-recovery](cedardb/backup-recovery/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | four million-row healthy/SIGKILL prerequisites and twelve sustained cells pass; 108,440 ACKs, zero loss/corruption, automatic-statistics/warm-query limits explicit; earlier append-only results remain separate |
| [ceph/s3-cluster](ceph/s3-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | four live faults, benchmark, cold reopen and fresh lifecycle/cleanup qualified above; extended categories remain unrun |
| [clickhouse/iceberg](clickhouse/iceberg/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [clickhouse/s3-tiered-storage](clickhouse/s3-tiered-storage/) | Compose | crash, disk | suite, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [duckdb/zfs-recovery](duckdb/zfs-recovery/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [garage/s3-cluster](garage/s3-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | live history/reader/native-copy recovery qualified above; extended categories require separate work |
| [greptimedb/docker-compose-cluster](greptimedb/docker-compose-cluster/) | Compose | suite, crash, disk, scale-out, scale-in | rolling, partition, pressure, restore, drain | declaration inventory only |
| [greptimedb/kubernetes-operator](greptimedb/kubernetes-operator/) | Kubernetes | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, drain | restore | declaration inventory only |
| [greptimedb/single-node](greptimedb/single-node/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [greptimedb/wal-bootstrap-reproducer](greptimedb/wal-bootstrap-reproducer/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [iceberg/multi-system](iceberg/multi-system/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [iceberg/trino-minio](iceberg/trino-minio/) | Compose | crash | suite, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [kafka/connect-postgres-enrichment](kafka/connect-postgres-enrichment/) | Compose | core broker/Connect/processor/PostgreSQL crashes, three diagnostics, fresh lifecycle and retained setup/recreation | disk, scale-out, scale-in, rolling, partition/quorum, pressure, restore, drain | individual core cases and independent fresh/retained lifecycles verified 2026-11-21; full aggregate invocation and extended matrix unrun |
| [kafka/cross-cluster-mirroring](kafka/cross-cluster-mirroring/) | Kubernetes | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | core live recovery, exact cutover, retained-state replay and scoped lifecycle qualified above; private kubeconfig and explicit contexts verified; four-case aggregate invocation remains unrun |
| [kafka/strimzi-jbod](kafka/strimzi-jbod/) | Kubernetes | suite | crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [lancedb/versioned-product-search](lancedb/versioned-product-search/) | Compose | test-production, writer-crash, reader-crash, maintenance, rolling-restart, cold-reopen, crash-reopen | partition, pressure, restore, SDK upgrade; native replication/sharding unsupported | core execution verified 2026-11-19; extended gaps above |
| [mysql/customer-sharding](mysql/customer-sharding/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | eight live fault cases qualified above; other categories require separate work |
| [mysql/ndb-cluster-docker-compose-cluster](mysql/ndb-cluster-docker-compose-cluster/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | eight live role cases qualified above; extended categories require separate work |
| [mysql/vitess-docker-compose-cluster](mysql/vitess-docker-compose-cluster/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | four live fault cases qualified above; extended categories require separate work |
| [postgres/citus-docker-compose-cluster](postgres/citus-docker-compose-cluster/) | Compose | suite, crash, scale-out, scale-in | disk, rolling, partition, pressure, restore, drain | ten live operations and placement-negative recovery qualified above; other categories require separate work |
| [postgres/customer-sharding](postgres/customer-sharding/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | six live fault cases qualified above; other categories require separate work |
| [postgres/neon/branching-recovery](postgres/neon/branching-recovery/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [postgres/neon/kubernetes-statefulset](postgres/neon/kubernetes-statefulset/) | Kubernetes | suite, crash, disk, restore | scale-out, scale-in, rolling, partition, pressure, drain | declaration inventory only |
| [postgres/neon/single-node](postgres/neon/single-node/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [postgres/pgdog-docker-compose-cluster](postgres/pgdog-docker-compose-cluster/) | Compose | suite, crash | disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | five live role/configuration cases qualified above; other categories require separate work |
| [postgres/pgvector-product-search](postgres/pgvector-product-search/) | Compose | test-production, failover, replica-crash, disk-failure, rebuilt-crash, cold-reopen, crash-reopen | scale-out, scale-in, rolling, partition, pressure, restore, drain | core execution verified 2026-11-19; extended gaps above |
| [qdrant/turboquant](qdrant/turboquant/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [rayforce/duckdb-comparison](rayforce/duckdb-comparison/) | Compose | suite | crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | DuckDB native WAL recovery passes; strict Rayforce payload/AOF integrity cases remain FAILED; do not infer a performance winner from diagnostic timings |
| [rustfs/s3-cluster](rustfs/s3-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | full fresh live fault aggregate, corrected cold reopen and two fresh lifecycles qualified above; extended categories remain unrun |
| [seaweedfs/s3-cluster](seaweedfs/s3-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | live faults, cold reopen and fresh lifecycle qualified above; extended categories remain unrun |
| [starrocks/docker-compose-cluster](starrocks/docker-compose-cluster/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [starrocks/kubernetes-operator](starrocks/kubernetes-operator/) | Kubernetes | suite, crash, disk, scale-out, scale-in, rolling, partition | pressure, restore, drain | declaration inventory only |
| [starrocks/single-node](starrocks/single-node/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [typesense/product-search](typesense/product-search/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [vespa/phased-product-search](vespa/phased-product-search/) | Compose | suite, crash, disk | scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
| [zfs/storage-lab](zfs/storage-lab/) | Compose | none | suite, crash, disk, scale-out, scale-in, rolling, partition, pressure, restore, drain | declaration inventory only |
