# Flink operator orders and checkpoint recovery

Flink 2.3.0 application mode managed by Apache Flink Kubernetes Operator 1.16.1.
Two one-slot TaskManagers process a million deterministic orders using native SQL
`SqlDriver`, with example-owned SeaweedFS 4.55 storing output and checkpoints.

## Quick start

1. `make kind-up` creates the owned three-node kind cluster and private kubeconfig.
2. `make up` builds local images, installs the operator and submits the SQL application.
3. `make test` requires native FINISHED state and independently verifies every output row.
4. `make down` removes the application, operator and owned namespace/storage.
5. `make kind-down` deletes the kind cluster, private kubeconfig and example-built images.

Prerequisites: Docker, kind, Helm, curl, Python 3 and shasum. `kind-up` installs
checksum-pinned kubectl 1.37.3 into ignored `results/tools/`, leaving host tools
unchanged. All commands explicitly use `kind-flink-kubernetes-operator` and a
private kubeconfig. A preflight requires 16 GiB free in Docker before creating
this lab. The operator chart is SHA-512 checked on install.

## Files and ownership

| Path | Responsibility |
|---|---|
| `deploy/kind.yaml` | One control-plane and two workers on one Docker VM |
| `deploy/operator-values.yaml` | Namespace-scoped operator configuration |
| `deploy/orders.yaml`, `sql/` | Native SQL application; one manager/two workers |
| `deploy/storage/base/` | Single owned S3 dependency/PVC with 32 volume slots |
| `deploy/storage/pressure/` | Preserved undersized four-slot failure profile |
| `scripts/objects.py` | Local-only S3 setup probe and exact independent body oracle |
| `scripts/job.py` | Native job/checkpoint gate, Pod fault and resource histories |
| `reports/` | Compact measured outcomes, including failed startup/stall findings |

The workload expects IDs 0–999,999, 10,000 customers and 599,500,000 cents. S3
uses only explicit lab endpoints/static credentials with metadata discovery
disabled. The source rate is intentionally bounded; these are correctness and
recovery diagnostics, not a capacity benchmark.

## Executed outcomes

| Case | Result |
|---|---|
| Initial setup | Blocked by Docker VM capacity, reaching 99 MiB free; owned teardown preserved evidence |
| Worker loss plus undersized S3 | Checkpoint restored; writable-volume exhaustion then stalled checkpoint 26 |
| Retained capacity recovery | 237,834 ms checkpoint; job finished with all 1M bodies correct after expansion |
| Fresh corrected-capacity run | Native FINISHED, 1M correct bodies, 51 completed checkpoints / one startup rejection |
| Repeated setup and separate test | Same resource UID/job ID and complete existing data retained |
| Local kubectl 1.37.3 checks | Repeated setup/readback passed; deployed SQL/oracle source hashes matched |

The clean run's rejected checkpoint occurred before all tasks were running.
It did not require a restore. The combined case included manual intervention and
is not a worker-only RTO. [Full result and resource bounds](reports/INITIAL_RESULTS.json)
· [Storage trigger and recovery procedure](docs/STORAGE_CAPACITY.md).

Initial controls used the host's unsupported kubectl 1.34.1/server 1.37.0 skew;
these observations remain labeled. The example now uses kubectl 1.37.3. Kind
0.33's pinned node is 1.37.0; a `kindest/node:1.37.3` artifact was unavailable at
qualification. See the [upstream client skew policy](https://kubernetes.io/releases/version-skew-policy/#kubectl).

## Other commands and remaining coverage

Use `make task-recovery` immediately after a fresh `make up`, instead of normal
`test`, to delete a worker after a completed checkpoint and require restoration
plus final exact output. `make storage-expand` recovers configured volume-slot
exhaustion on retained storage. Faults and their evidence are separate from
benchmarks. `make report` curates completed evidence without running a benchmark.

Resource histories contain per-container CPU, memory, disk byte/operation counters
and rates where observed. Device latency/queue depth, full network accounting,
short-lived oracle cost and control-plane overhead remain incomplete. Reference
gzip is separate from native S3 storage compression/reclamation.

All kind nodes share one physical host. SeaweedFS is one non-HA dependency with a
local PVC, not an independent backup. Admission webhook/cert-manager is disabled
in this initial profile. Live external ACK witnesses/readers, savepoint-driven
scale-out/in, manager HA, node/disk loss, independent restore, semantic continuous
readiness and a matched level-3 comparison remain pending.

[Declared contract](config/contract.json)
· [Compatible stable releases](https://flink.apache.org/downloads/)
· [Native SQL driver source](https://github.com/apache/flink/blob/release-2.3.0/flink-table/flink-table-runtime/src/main/java/org/apache/flink/table/runtime/application/SqlDriver.java)
