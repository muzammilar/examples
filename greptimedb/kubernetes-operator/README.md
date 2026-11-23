# GreptimeDB: official Kubernetes operator and recovery

Three datanodes own three regions per workload table. Three Kafka-compatible WAL
brokers and replicated S3 retain durable data independently of those datanodes.
This lab exercises the official operator, region movement, rolling resource
changes and failure/recovery while a separately journaled SQL producer runs.

## Quick start

Prerequisites: Docker, Make, Python 3, kind, kubectl and Helm (`nix develop`).
Allow 20 GiB Docker RAM and 10 GiB free disk for this example alone. Versions are
pinned; all database/workload images used here run natively on ARM64.

1. `make kind-up` — build the client, start owned durable dependencies and create the private kind cluster.
2. `make up` — install the operator/cluster, create the client pod and load the corpus.
3. `make test` — verify every metric value/timestamp and log body/timestamp in both systems, plus recovery histories.
4. `make down` — delete workload tables/pipeline, Helm releases and the example namespace.
5. `make kind-down` — delete the owned kind cluster/context/config and support services, volumes and local images.

The cluster is `greptime-operator`, its explicit context is
`kind-greptime-operator`, and its private kubeconfig is `results/kubeconfig`.
The Makefile exports that file before kind creation. Every kubectl/Helm command
specifies the context. No user default context is selected or changed.

## Topology and resources

| Component | Version / configuration | Per-container limits |
|---|---|---|
| kind | 1 control plane + 3 workers, Kubernetes v1.34.0 | Shared Docker VM; no independent host failures |
| Official operator | 1.3.0; cluster chart 1.3.3 | 500m CPU / 256 MiB |
| GreptimeDB | 1.3.1; 3 Metasrv, 3 datanodes, 2 frontends | Metasrv 1 CPU / 512 MiB; datanode 1 CPU / 1 GiB; frontend 1 CPU / 768 MiB |
| Durable remote WAL | Redpanda 26.2.3; 3 brokers, RF=3, 12 WAL topics; write cache disabled | See support Compose config |
| Metadata | etcd 3.6.16; 3 members | See support Compose config |
| Object storage | Garage 2.5.0; 9 peers, 3 logical zones, RF=3, fsync | See support Compose config |
| Metrics baseline | VictoriaMetrics 1.156.0; 3 vmstorage + vminsert/vmselect | See support Compose config |
| Logs baseline | VictoriaLogs 1.54.0; single instance | See support Compose config |
| Workload pod | Go client, official Prometheus protobuf/Snappy libraries | 2 CPUs / 2 GiB |

Durable dependencies and Victoria baselines run in the **owned** Compose project
`greptime-kind-support`. Generated Services/EndpointSlices bridge its label-verified
Docker addresses into Kubernetes. Only GreptimeDB and its operator are deployed
through the official Kubernetes operator. This is not a test of an etcd, Redpanda
or S3 Kubernetes operator. Aggregate service CPU limits oversubscribe the shared
11-CPU VM; the baseline stacks have different durability and dependency costs.

`SERIES=10000 POINTS=100 LOGS=1000000` gives **1,000,000 metric samples and
1,000,000 logs per backend**. The deterministic historical backfill uses real event
timestamps; every value/body is read back. Metrics use three host ranges, logs three tenant ranges, and recovery rows
three customer ranges as native GreptimeDB regions. A datanode is a region owner, not one of three hot replicas;
WAL and object-storage replication provide distinct durable copies.

## Independent operational tests

Run after `make up`; `make test-production` runs the implemented cases serially.
Each case uses a new journal name and preserves previous acknowledged histories.

| Command | Injection and recovery |
|---|---|
| `make scale-out` / `make scale-in` | CR changes 3 → 5 → 3 datanodes; native `ADMIN migrate_region` places regions on added nodes and drains **all** regions before removal. |
| `make scale-up` / `make scale-down` | CR rolls datanode limits from 1 CPU / 1 GiB to 1.5 CPUs / 1.5 GiB, then back; verify actual running resources. |
| `make rolling-restart` | Change the datanode template annotation through the CR; require all three replacement Pod UIDs and native convergence. Same-version maintenance, not an upgrade test. |
| `make failover` | Delete the owning datanode pod; verify the old runtime container exited, a new UID is Ready and subsequent writes succeed. |
| `make disk-failure` | Scale to two datanodes, fence the stopped member, delete its exact owned local PVC, wait for native region failover, restore three and move existing data onto the new empty cache. Remote WAL/S3 remain intact. |
| `make chaos-node` | Stop the owned third kind worker, observe Node NotReady and acknowledged writes while unavailable, then restart its retained container and verify all data. The client remains on another worker. |
| `make chaos-operator` | Scale the operator deployment to zero; submit a frontend template change through the CR. Require continued data-plane writes and no reconciliation until the operator returns; then require the pending rollout. |
| `make chaos-wal-quorum` | Stop two of three WAL brokers for 30 seconds. After a 10-second allowance for pre-fault completions, require no new write acknowledgements. Restore brokers, wait for native WAL health, then require new writes and full reconciliation. |

`make test-resilience` independently runs these additional cases and full verification;
`test-production` includes it after the existing ten cases. Upgrade is a separate
fresh-old-version workflow, so ordinary tests never downgrade populated storage.

| Command | Injection and recovery |
|---|---|
| `make drain` | Normal eviction of one owned worker with workload witness elsewhere. Datanode/Metasrv/frontend PDBs retain minima 2/2/1; require actual rejection of an unsafe second datanode eviction, uncordon and verify native recovery. Local-PV data cannot reschedule while its worker is cordoned. |
| `make chaos-network` | Discover a real region owner and drop its peer TCP traffic (Metasrv 3002 / datanode 4001) using an owned worker chain for 60 seconds. Require nonzero DROP counters, heal and verify native regions/data. WAL/S3 and HTTP health are not isolated. |
| `make chaos-oom` | Roll datanodes to 32 MiB request / 64 MiB limit, require native OOMKilled, restore original CR limits, then recreate only proven OOM victims if StatefulSet rollback retains an unready bad-revision Pod. PVCs remain intact. |
| `make chaos-cpu` | Roll to 100m CPU, run bounded high-cardinality analytical queries alongside live writers/readers, prove effective cgroup `cpu.max` and increased throttling, restore resources and verify full data. |

For a pinned forward patch upgrade, start a fresh lifecycle with
`make kind-up DB_VERSION=v1.3.0`, `make up DB_VERSION=v1.3.0`, then `make upgrade`.
This changes `spec.base.main.image` to v1.3.1 through the operator, waits for all
3 metadata + 3 data + 2 frontend Pods at the new image and reconciles original data.
Do not run this after upgrading: it refuses a no-op. `make up DB_VERSION=v1.3.0`
also rejects an existing v1.3.1 fixture before Helm; rollback/downgrade is unqualified.

Every producer logs fsynced attempts/ACKs outside database PVCs, uses unique IDs,
and keeps ambiguous batches separate. At least three new batches must be
acknowledged after recovery before stopping it. Full readback reconciles all
historical journals, including earlier experiments; reads from a survivor alone
do not count as recovery. The fault workload sleeps 100 ms between 100-row batches;
its offered rate is deliberately bounded and is not a capacity benchmark.

## Failures found and recovery

The fresh fixture passed bootstrap, full baseline readback, benchmarks and
horizontal scale-out/in. Vertical scale-up then failed full readback. The old
message `missing 666600` identified the **first missing sample index**, not a
missing count. Subsequent queries through both frontends found **996,600 / 1,000,000
samples**: hosts `h006666` through `h006699` were absent (3,400 samples). Logs
remained complete. Ready pods and ALIVE region metadata did not detect this loss.

The affected region's restart log replayed 330,000 rows; its prior in-memory
sequence represented 333,400. Kafka topic `kind_telemetry_wal_4` had that region's
136,082-byte application entry at **offset zero**. The pinned opener replays from
`flushed_entry_id + 1`; the empty manifest starts at zero. Source topic preparation
normally reserves offset zero with a no-op. The metadata leader logged `Failed to notify listener: WalProvider` after its
three-second partition-client deadline expired with `LeaderNotAvailable` during
topic preparation. It then continued serving requests without a successful topic
pool activation. The new [minimal WAL bootstrap reproducer](../wal-bootstrap-reproducer/)
is independently executed: the unsafe fixture loses 100/100 ACK rows after SIGKILL,
while a separately initialized healthy control retains 100/100. It isolates missing-topic
bootstrap ordering, rather than claiming the original LeaderNotAvailable trigger
is identical.

Bootstrap now precreates the 12 owned topics and waits for broker health before
Helm installation, gives metadata WAL connections a 30-second timeout, and rejects
ingestion unless every topic has the native no-op at offset zero. The guard was
verified to reject the preserved failed initialization. This prevents silently
admitting writes after that initialization failure; it does not repair existing
unsafe WAL entries or patch the upstream listener error handling.

`python3 scripts/repair_boundary_batch.py` explicitly replayed only the 34 absent hosts
from the external deterministic fixture, without dropping tables or resetting
storage. It refuses a different fixture/count, a populated target range or a
previous repair attempt. Its fsynced report is separate from the original failed
case. Full readback then passed all four million baseline records plus **29,400
historical ACK rows across three journals**. This is manual application repair,
not proof of automatic recovery or survival of those original 3,400 ACKed samples.
Do not run this fixture-specific repair automatically in production tests.

The worker restart also exposed a dual-network kind gotcha: its entrypoint
rediscovered the secondary Docker address and rewrote kubelet's `--node-ip`.
The worker became Ready with the wrong InternalIP; two database members could not
reconnect. `python3 scripts/pin_node_ips.py greptime-operator-worker3` restored its kind
address and restarted kubelet, with volumes retained. That case is **manually
recovered**, not an automatic node-recovery pass. The next clean setup connects
the support network with Docker `--gw-priority -1`, retaining kind as the primary
network. An earlier fresh worker restart retained the correct kind InternalIP,
but the later expanded suite reproduced the wrong-address failure despite
`--gw-priority -1`. Gateway priority controls routes, not which hostname DNS answer
kind chooses during restart. The pinned kind entrypoint reads `getent ahostsv4`
and rewrites `kubeadm-flags.env`; either bridge address can be returned first.

Setup now installs an explicit final `--node-ip` in the supported
`/etc/default/kubelet` extra-argument file, preserving the runtime-cgroup argument.
The entrypoint does not rewrite this file. `pin_node_ips.py` validates exact kind
ownership/address, checks effective process arguments and preserves a dated report.
This is an explicit lab networking workaround, not database-native recovery. The
failed worker attempt remains failed; its repair retained all database volumes.

The recovery controllers now wait for full baseline counts through both
frontends while readers/writers remain live, then perform complete body/value
reconciliation. A clean recreated fixture passed every implemented case without
manual data repair. Its WAL initialization guard accepted all 12 native no-ops.
The guard separately rejected the earlier unsafe fixture.

Operator 1.3.0 updates CR `observedGeneration` only when its phase changes. A fast
scale-in left that field stale despite converged children. The first resize
precheck was interrupted without injection; both actual resize directions were
then run explicitly. Readiness now checks desired counts/resources/annotations,
child-controller generations/rollouts, native regions and complete frontend
visibility. A resize already at its target limits is rejected as a no-op.

Original failed/manual-recovery evidence is preserved in
`results/archive-20261116T000220.294680/`, including the affected original WAL
entry, metadata startup error and explicit application-repair journal.

## Expanded qualification: November 16 follow-up

A fresh fixture initialized on **v1.3.0** was upgraded to **v1.3.1** while live
writers/readers continued. All ten existing production cases and four additional
resilience cases ultimately passed, plus the separate patch upgrade: **15 distinct
cases**. The aggregate stopped at the node-address failure described above; after
retained-state repair, its node/operator/WAL targets and the four resilience cases
were resumed individually. This is not a claim that the original aggregate exited
successfully. No data reset concealed the failed upgrade/CPU/node attempts.

Latest additional-case runs on the same stated 11-CPU / 24.42 GiB ARM64 VM:

| Case | Transition + full verification s | ACK rows | Ambiguous write requests | Read errors |
|---|---:|---:|---:|---:|
| Forward database patch upgrade | 77.549 | 43,100 | 2 | 6 |
| Worker drain and unsafe second-eviction rejection | 27.626 | 13,800 | 0 | 0 |
| Real peer partition, 60-second hold | 77.167 | 59,700 | 0 | 2 |
| Bounded OOM and explicit retained-PVC recovery | 28.783 | 15,000 | 0 | 0 |
| 100m CPU limit with two analytical readers | 88.855 | 40,000 | 2 | 11 |

The partition recorded **51 dropped packets** across its two rules. The CPU test
completed **43/43** validated high-cardinality queries and measured **65 and 300**
additional throttled periods on two active datanodes, with stable container IDs and
native `cpu.max=10000 100000`. All three limits were restored afterward. OOM was
proven by native `OOMKilled`; rollback explicitly recreated the stuck bad-revision
Pod after restoring its CR resources. These are bounded fault experiments, not
capacity benchmarks. Phase reports retain separate successes, errors and latency
percentiles; the independent watcher observes immutable known metric values.

Final verification read **all four million baseline records** and reconciled
**627,200 ACK rows across 23 journals**: **0 missing ACKs, 0 wrong values and 0
unexpected/duplicate rows**. The journals include four failed attempts and repeated
resilience runs; neither counts as additional distinct passing cases. The corrected
worker restart retained its kind IP and data without a post-fault manual repair.
The downgrade guard accepted existing v1.3.1 and rejected v1.3.0 before Helm.

Recovered-state benchmark: one warmup, **20 validated requests per query/backend**,
**120/120 successful requests**, p99 is the largest sample. Original resource limits
were restored. This is a single shared-host run, not saturation or an equal-budget
capacity ranking.

| PromQL | Greptime p50 / p99 ms | VictoriaMetrics p50 / p99 ms |
|---|---:|---:|
| `sum(fleet_load)` | 45.034 / 50.533 | 26.346 / 35.942 |
| `sum by (tenant) (fleet_load)` | 50.105 / 57.951 | 26.862 / 38.763 |
| `max_over_time(fleet_load[60s])` | 112.405 / 119.480 | 47.563 / 49.002 |

Raw native/controller/reader reports and original failures remain in ignored
`results/`. The earlier qualification below is retained as dated history.

## Measured results

2026-11-16, Apple M4 Pro, shared aarch64 Docker VM, 11 CPUs / 24.42 GiB.
The final fixture verified 1,000,000 metric samples and 1,000,000 logs per backend
(all four million records), with zero missing, duplicate or wrong values.
Budgets are listed above; unrelated host workloads were not isolated. Resize tests
change datanodes to 1.5 CPUs / 1.5 GiB and restore 1 CPU / 1 GiB before benchmarking.

Ingestion rates use **active API-request time**, excluding generation, encoding
and witness fsync. They measure bounded synthetic backfill admission, not sustained
capacity or equivalent durable-commit latency. All 2,200 requests succeeded.

| Backend / signal | Records | API-active records/s | Batch p50 / p99 ms |
|---|---:|---:|---:|
| greptime/logs | 1,000,000 | 286,734 | 3.373 / 6.045 |
| greptime/metrics | 1,000,000 | 902,867 | 10.748 / 17.500 |
| victoria/logs | 1,000,000 | 527,035 | 0.838 / 20.791 |
| victoria/metrics | 1,000,000 | 5,334,672 | 0.644 / 13.279 |

One warmup precedes 20 measured serial queries per backend. Every returned vector
is checked against an independent oracle; p99 is the maximum of this small sample.

| PromQL | Greptime p50 / p99 ms | VictoriaMetrics p50 / p99 ms |
|---|---:|---:|
| `sum(fleet_load)` | 55.666 / 58.905 | 26.306 / 32.445 |
| `sum by (tenant) (fleet_load)` | 61.776 / 67.250 | 27.592 / 33.149 |
| `max_over_time(fleet_load[60s])` | 152.601 / 169.400 | 49.816 / 51.954 |

| Case | Native transition/recovery s | ACK rows | Ambiguous write requests | Read errors | Missing/wrong known reads |
|---|---:|---:|---:|---:|---:|
| scale-out | 13.271 | 15,600 | 0 | 0 | 0 |
| scale-in | 3.779 | 6,700 | 0 | 0 | 0 |
| scale-up | 22.393 | 14,100 | 1 | 4 | 0 |
| scale-down | 22.314 | 14,200 | 1 | 3 | 0 |
| rolling | 22.432 | 14,200 | 1 | 5 | 0 |
| process | 6.365 | 9,200 | 0 | 3 | 0 |
| disk | 41.166 | 32,100 | 1 | 13 | 0 |
| node | 108.746 | 42,000 | 8 | 52 | 0 |
| operator | 43.313 | 40,900 | 1 | 0 | 0 |
| wal-quorum | 48.756 | 14,600 | 3 | 0 | 0 |

These include deliberate 30-second holds where configured, native catch-up and
polling; they are not exact election timestamps or capacity benchmarks. Native
WAL minority loss produced **zero ACKs after the ten-second grace window**.
Readers sample immutable known baseline values, not newly written row freshness.
Raw phase reports separate successful/failed read and write latencies.

Full reconciliation retained **207,000 ACK rows across 11 journals**, with zero
missing ACKs, wrong values or unexpected/duplicate rows. Of these, 3,400 rows came
from the initial no-op scale-down attempt; that attempt is not a qualifying resize.
The table uses the subsequent actual downsize. An additional 67 attempted but
unacknowledged rows were present; failed responses do not imply absent writes.
`fresh-matrix-validation.json` records all ten completed case reports and no manual
data repair. The final benchmark and full five-command teardown passed. No owned kind nodes,
support containers/volumes, generated identities or private kubeconfig remained.

## Gotchas and remaining coverage

- Native `cluster_info` retains drained historical peers. Scaling intersects native
  RPC addresses with currently Ready datanode pod IPs instead of counting old rows.
- Set `meta.enableRegionFailover: true` as well as the database's native failover
  configuration. The operator uses the CR field to manage maintenance during
  datanode rolling updates; a database-only setting misses that operator path.
- An early pod-crash attempt stopped the producer after pod readiness, before a
  post-recovery write succeeded. That attempt is retained but does not establish
  live write recovery; corrected runs require fresh acknowledgements.
- Local-PVC replacement is **cache loss**, not loss of all durable S3/WAL copies,
  a Linux EIO injection or a physical disk/power failure. Worker restart retains
  local storage. All logical zones share one physical machine.
- The first two-broker crash/restart attempt failed automatic recovery. Docker
  reassigned broker addresses; stale bridge endpoints and wrong-target-node RPC
  replies were observed. Recovery required `python3 scripts/config.py endpoints` using the
  private kubeconfig and restarting the retained `greptime-kind-support-wal0-1`
  broker, then native health and full readback. No volumes/data were reset. The
  test now refreshes endpoints and clears the survivor peer connections when its
  recorded broker addresses change. This is a lab discovery/configuration limit,
  not evidence of a Redpanda data-loss bug. Direct Python controllers also select
  the private kubeconfig, independent of shell defaults.
- The WAL-quorum case intentionally makes writes unavailable; that is the expected
  durability boundary, not an availability pass for a minority.
- Operator 1.3.0 derives `spec.version` from `spec.base.main.image` during
  defaulting. Changing only version was overwritten and correctly failed the test;
  no database Pod upgraded in that attempt. The corrected forward upgrade changes
  the image too. All its existing ACK data is retained in subsequent checks.
- Normal drain proved the unsafe second datanode eviction was rejected. An empty
  replacement remains pinned to its local PVC's node until uncordon; adding a Pod
  elsewhere does not migrate the underlying local disk.
- The OOM experiment required recreating the proven bad-revision Pod after restoring
  its CR memory limits: StatefulSet rollback alone can wait on an unready Pod.
  This intervention is explicit in its report; it is not automatic OOM recovery.
- The first CPU probe assumed a `/kubepods.slice` hierarchy, while this kind image
  nests it under `/kubelet.slice`. The probe now validates the actual Pod UID and
  container ID. A second light-load run applied 100m but produced no additional
  throttling and remains failed; the pressure workload adds bounded real queries.
- Not run here: permanent worker loss/local-PV rescheduling, operator crash during
  maintenance, invalid CR admission, operator/CRD upgrades, backup restore,
  disk-full pressure, multi-host power failure and sustained saturation/soak tests. These remain production-test
  gaps, not unsupported features or passing claims.
- Pipeline deletion uses explicit versions and the pinned server's exact absence
  response. Full VictoriaLogs readback uses bounded event-time windows to avoid
  an implicit million-row sort exceeding its query-memory cap.
- Plaintext endpoints and committed local-only lab credentials are not a production
  security configuration. No authentication/TLS qualification is claimed.

## Sources

- [Official operator configuration](https://docs.greptime.com/user-guide/deployments-administration/deploy-on-kubernetes/common-helm-chart-configurations/).
- [Pinned operator API](https://github.com/GreptimeTeam/greptimedb-operator/blob/v1.3.0/apis/v1alpha1/greptimedbcluster_types.go).
- [Pinned image-to-version defaulting](https://github.com/GreptimeTeam/greptimedb-operator/blob/v1.3.0/apis/v1alpha1/defaulting.go).
- [Pinned controller phase/generation updates](https://github.com/GreptimeTeam/greptimedb-operator/blob/v1.3.0/controllers/greptimedbcluster/controller.go).
- [Docker gateway priorities](https://docs.docker.com/engine/network/#connecting-to-multiple-networks).
- [Pinned datanode reconciliation and maintenance](https://github.com/GreptimeTeam/greptimedb-operator/blob/v1.3.0/controllers/greptimedbcluster/deployers/datanode.go).
- [Pinned WAL replay opener](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/mito2/src/region/opener.rs), [topic initialization](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/common/meta/src/wal_provider/topic_creator.rs) and [Kafka offset mapping](https://github.com/GreptimeTeam/greptimedb/blob/v1.3.1/src/log-store/src/kafka/log_store.rs).
- [Pinned database configuration](https://github.com/GreptimeTeam/greptimedb/tree/v1.3.1/config).

## Source and build

`telemetry-workload/` is a tracked symlink to this system’s workload source.
The first lifecycle command runs `prepare.py` to materialize regular files in
`.build/telemetry-workload/`, which is the local Docker build context. `.build/` is
gitignored and is never committed. The application runs as this example’s
Compose service or Kubernetes workload Pod; its lifecycle needs no sibling
example’s running services or generated state.

Kubelet override reference: [kubeadm kubelet integration](https://kubernetes.io/docs/setup/production-environment/tools/kubeadm/kubelet-integration/).

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
