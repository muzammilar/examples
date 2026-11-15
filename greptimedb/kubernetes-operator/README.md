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

Every producer logs fsynced attempts/ACKs outside database PVCs, uses unique IDs,
and keeps ambiguous batches separate. At least three new batches must be
acknowledged after recovery before stopping it. Full readback reconciles all
historical journals, including earlier experiments; reads from a survivor alone
do not count as recovery. The fault workload sleeps 100 ms between 100-row batches;
its offered rate is deliberately bounded and is not a capacity benchmark.

## Current validation status

The latest final-source run passed bootstrap, full baseline correctness, query
benchmarks and horizontal scale-out/in. The next vertical scale-up failed full
readback: the verifier reported **666,600 missing metric samples**. The cause is
unresolved; this observation does not yet establish permanent data loss. The
cluster and journals are preserved, with no data reset. Remaining production
cases and teardown have not completed on this revision.

The numbers below describe an **earlier revision**, before the final live-reader
and build-layout changes. They are historical evidence, not qualification of the
current source. Current failures are retained in ignored `results/`.

## Historical measured results

2026-11-15, Apple M4 Pro, shared Docker VM aarch64, 11 CPUs / 24.42 GiB.
One baseline backfill and one 20-request warm-query series per query/backend;
one or more operational attempts per case. Limits are listed above and in Compose.
All four million baseline records passed complete value/body/timestamp readback.

Ingest rates below divide records by **active API-request time only**. Generation,
encoding and journal fsync are excluded from that denominator. These are bounded
historical backfill/admission measurements, not sustained capacity, equal-durability
rankings or end-to-end throughput. Request timing includes transport and the server
response; it does not independently establish storage visibility/durable-commit latency.

| Backend / signal | Records | API-active records/s | Batch p50 / p99 (ms) | Request errors |
|---|---:|---:|---:|---:|
| greptime/logs | 1,000,000 | 296,509 | 3.195 / 6.017 | 0 |
| greptime/metrics | 1,000,000 | 911,364 | 10.022 / 28.656 | 0 |
| victoria/logs | 1,000,000 | 543,777 | 0.870 / 18.843 | 0 |
| victoria/metrics | 1,000,000 | 5,005,213 | 0.560 / 14.806 | 0 |

Each metric vector was checked independently against deterministic expected results.
One warmup precedes 20 measured queries; p99 is the largest of only 20 observations.

| PromQL | Greptime p50 / p99 (ms) | Victoria p50 / p99 (ms) | Greptime/Victoria p99 ratio |
|---|---:|---:|---:|
| `max_over_time(fleet_load[60s])` | 192.150 / 208.327 | 50.270 / 59.286 | 3.51× |
| `sum by (tenant) (fleet_load)` | 69.397 / 80.878 | 28.742 / 33.205 | 2.44× |
| `sum(fleet_load)` | 63.630 / 69.412 | 27.431 / 33.103 | 2.10× |

Latest completed operational run per case:

| Case | Transition (s) | ACK rows in case | Ambiguous batches | Longest inter-ACK gap (s) | Missing ACK / wrong / duplicate |
|---|---:|---:|---:|---:|---|
| scale-out | 7.007 | 8,200 | 0 | 0.120 | 0 / 0 / 0 |
| scale-in | 3.822 | 5,400 | 0 | 0.117 | 0 / 0 / 0 |
| scale-up | 21.540 | 13,700 | 1 | 18.714 | 0 / 0 / 0 |
| scale-down | 21.595 | 7,900 | 1 | 16.033 | 0 / 0 / 0 |
| rolling | 21.514 | 21,600 | 0 | 0.122 | 0 / 0 / 0 |
| process | 6.330 | 7,800 | 0 | 0.129 | 0 / 0 / 0 |
| disk | 41.237 | 39,300 | 0 | 0.220 | 0 / 0 / 0 |
| node | 86.735 | 80,700 | 0 | 0.121 | 0 / 0 / 0 |
| operator | 42.292 | 40,500 | 0 | 0.127 | 0 / 0 / 0 |
| wal-quorum | 54.057 | 19,900 | 5 | 34.224 | 0 / 0 / 0 |

Transition times cover command/rollout or region-migration convergence, not exact
election instants; disk and WAL cases include deliberate 30-second holds. First
post-injection ACK may complete an operation started before injection. Each passing
case additionally required three fresh ACKs **after** convergence. Inter-ACK gaps
come from the external journal; ambiguity counts cover the entire case.

The WAL-quorum rerun includes an initial wait for recovery from the previous failed
attempt, so its full-case ambiguity count must not be attributed solely to the
new injection. Its enforced minority window had **0 acknowledged rows after the
10-second grace interval**. Native recovery took 54.057 s including the 30-second
hold; final full verification took 71.078 s. These timings exclude that initial wait.

Full historical reconciliation checked **375,600 acknowledged rows across 18 journals**,
with **0 missing ACKs, 0 wrong values and 0 unexpected/duplicate rows**.
This includes journals from failed/incomplete attempts; retaining their data does
not turn those attempts into passing recovery tests. The full five-command lifecycle and cleanup passed; no owned kind nodes, support
containers/volumes or private kubeconfig remained. Raw results live in ignored
`results/`, including `*-failure.json`, request journals and native health output.

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
  replies were observed. Recovery required `python3 config.py endpoints` using the
  private kubeconfig and restarting the retained `greptime-kind-support-wal0-1`
  broker, then native health and full readback. No volumes/data were reset. The
  test now refreshes endpoints and clears the survivor peer connections when its
  recorded broker addresses change. This is a lab discovery/configuration limit,
  not evidence of a Redpanda data-loss bug. Direct Python controllers also select
  the private kubeconfig, independent of shell defaults.
- The WAL-quorum case intentionally makes writes unavailable; that is the expected
  durability boundary, not an availability pass for a minority.
- Not run here: PDB/drain behavior, permanent worker loss/local-PV rescheduling,
  native network partition, operator crash during maintenance, invalid CR changes,
  database/operator upgrades, backup restore, disk-full/OOM pressure, multi-host
  power failure and sustained saturation/soak tests. These remain production-test
  gaps, not unsupported features or passing claims.
- Pipeline deletion uses explicit versions and the pinned server's exact absence
  response. Full VictoriaLogs readback uses bounded event-time windows to avoid
  an implicit million-row sort exceeding its query-memory cap.
- Plaintext endpoints and committed local-only lab credentials are not a production
  security configuration. No authentication/TLS qualification is claimed.

## Sources

- [Official operator configuration](https://docs.greptime.com/user-guide/deployments-administration/deploy-on-kubernetes/common-helm-chart-configurations/).
- [Pinned operator API](https://github.com/GreptimeTeam/greptimedb-operator/blob/v1.3.0/apis/v1alpha1/greptimedbcluster_types.go).
- [Pinned datanode reconciliation and maintenance](https://github.com/GreptimeTeam/greptimedb-operator/blob/v1.3.0/controllers/greptimedbcluster/deployers/datanode.go).
- [Pinned database configuration](https://github.com/GreptimeTeam/greptimedb/tree/v1.3.1/config).

## Source and build

`telemetry-workload/` is a tracked symlink to this system’s workload source.
The first lifecycle command runs `prepare.py` to materialize regular files in
`.build/telemetry-workload/`, which is the local Docker build context. `.build/` is
gitignored and is never committed. The application runs as this example’s
Compose service or Kubernetes workload Pod; its lifecycle needs no sibling
example’s running services or generated state.
