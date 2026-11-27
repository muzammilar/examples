# Doris operator: replicated mutable orders

Initial correctness/recovery checks passed on 2026-11-27. A private three-worker kind cluster runs
Doris 4.0.8 with Operator 26.0.1, three voting FEs and three BEs. Each role has
separate persistent storage and hard anti-affinity across workers. The intended
workload is the same million-order sequence-controlled upsert used in Compose.

## Quick start

1. `make kind-up` creates this example's four-node kind cluster/private kubeconfig.
2. `make up` builds the Go workload, loads pinned images and installs the operator/database.
3. `make test` seeds one million rows and verifies all three fixed replica indexes.
4. `make down` removes the example operator/database namespace and owned storage.
5. `make kind-down` removes its kind cluster, private kubeconfig and workload image.

Requires Docker, kind, Make, Python 3, Git and shasum. Checksum-pinned kubectl
1.37.3 is installed locally under ignored `results/tools/`. Every cluster command
specifies `kind-doris-kubernetes-operator`; kind uses this directory's kubeconfig.
No user context, Docker disk settings, AWS configuration or real cloud resources
are changed. All connections stay inside this local cluster.

## Files and preparation

- `orders-workload/`: meaningful links to tracked Go source plus a local Dockerfile.
- `config/`: source/image pins, kubectl checksums and native FE/BE configuration.
- `deploy/`: private kind, database CR and versioned-operator rendering patches.
- `scripts/`: source materialization, capacity checks, resource observer and workload Jobs.
- `reports/`: curated executed results, when available. Raw evidence is ignored under `results/`.

`make prepare` hardlinks/copies tracked Go source into ignored `.build/`, downloads
the checksum-pinned Apache operator source and selects its exact namespace-scoped
installation manifests. Kustomize pins its image and watched namespace, disables
unused disaggregated controllers/webhooks and bounds resources. The source archive
and rendered manifests are generated artifacts, not vendored source to commit.
The upstream chart repository did not publish 26.0.1 when checked; no older chart
is silently substituted. Two upstream CRD filenames contain the same CRD identity;
only the `doris.selectdb.com` file is rendered. The included public sample TLS
Secret is unused while webhooks are disabled; it is not a production credential.

FE/BE configurations come from their pinned official images. The FE heap is 1 GiB;
BEs have native `mem_limit=2G`, `num_cores=1`, and 3 GiB container limits. FEs have
2 GiB limits, operator 512 MiB and Go Jobs 256 MiB. Baseline role caps total about
15.75 GiB plus kind/control-plane overhead. These are small-data lab budgets,
not production sizing. Pod CPU/memory and native values must be read back before
qualification. The default privileged system initializer is disabled to avoid
changing the Docker VM's global sysctls. Record effective kernel limits; a small
successful run cannot qualify production map-count, file-limit or power-loss behavior.

## Resource and evidence bounds

The large FE/BE images unpack to approximately 3.85/8.22 GB, with shared layers.
The preflight requires 42 GiB free before kind creation, then conservatively gates
missing worker imports and keeps at least 6 GiB runtime headroom. Imports run
serially per worker and explicitly select the Docker VM native platform. The
initial standard kind import rejected a cached multi-platform index referencing
missing foreign-platform content; its failure is preserved. This is a setup
compatibility problem, not a measured database data failure. Docker inspect's compressed image Size is not an unpacked
storage reservation. Failed/partial setup is preserved and its owned cluster
must be torn down before another heavy example. No automatic global image or
volume prune is part of the lifecycle.

`python3 scripts/observe.py up` or `... test` captures timestamped native kubelet
node/Pod CPU, memory, network and filesystem counters into separate result files.
Kubelet stats do not expose complete block I/O operations/service latency; those
measurements and short-lived actor gaps remain unqualified until supplemented.
Functional correctness cannot establish a holistic benchmark winner.

## Initial contract and remaining tests

Eight hash tablets, RF3, one million rows and 10,000 customers; with three BEs,
each BE stores a copy of every tablet. This verifies replication, not capacity
scale-out or tenant-exclusive placement. Every field must
match its deterministic revision. `mutate` upserts revision 2 and independently
verifies each fixed replica index. Native topology must prove three live FE voters,
one leader, three BEs and 24 current NORMAL tablet replicas. Queries disable result
caches and fallback when selecting one replica. Finite bulk SQL is one mutation
ACK, not a per-row external acknowledgement journal.

`backend-restart` and `operator-restart` passed as quiesced retained-data
cases. `test-production` ran the functional test and these two cases serially;
`mutate` subsequently passed all three replica reads at revision 2. Native
placement and BE core/memory settings were read back. Required next checks include
retained FE crash, repeated setup against acknowledged data and live workload/witness histories,
scale-out/in with safe decommission, node restart/drain/PDB, partitions/quorum
loss, rolling upgrades, disk loss, pressure and independent backup/restore. Three
workers cannot accommodate a fourth BE under hard anti-affinity; scaling requires
an explicitly budgeted fourth worker, not silently co-locating another replica.
Three kind workers still share one Docker VM and physical failure domain.


## Measured first pass

[Curated report](reports/INITIAL_RESULTS.json) preserves the twelve full-body
verification results, native topology, recoveries, effective configuration,
per-role cgroups/storage and timestamped kubelet resource summaries.

| Check | Result |
|---|---|
| Revision 1 | 1,000,000 exact orders; 10,000 customers; 599,500,000 cents on each fixed replica |
| Revision 2 upserts | Same 1,000,000 IDs, revision 2 throughout; 1,599,500,000 cents on each replica |
| Native membership/placement | Three voting FEs, one leader, three BEs on three workers; eight tablets with 24 current NORMAL replica records |
| Retained BE SIGKILL | Exact containerd task killed; native exit 137 and restart observed; Pod/PVC identities unchanged; all replica reads passed |
| BE recovery workflow elapsed | 20.315 s, including capacity wait and three complete verification Jobs; not an isolated failover RTO |
| Operator restart workflow | 27.273 s, including rollout and complete verification; database Pod UIDs and restart counts unchanged |
| Revision-2 query/fetch/body verification | 304.655–439.534 ms across three replica selections; median 425.315 ms, diagnostic trials only |
| Native table size after reports caught up | `SHOW DATA`: 11.879 MB, 24 replicas, 1,000,000 rows |
| BE directories | 12.60–12.96 MB logical each; 94.91–94.93 MB allocated each, including preallocation/metadata and obsolete versions |
| Effective BE settings | `num_cores=1`, `mem_limit=2G` on every BE, within 1 CPU/3 GiB Pod caps |
| Current-container native charged memory peaks | Maximum 1,604,521,984 bytes across captured database containers; excludes the killed incarnation's final interval |

The artifact is pinned as 4.0.8; native FE membership reports
`doris-4.0.8-rc02-bc8ea1bac6d`. SQL `VERSION()` reports MySQL compatibility
`5.7.99`, so it is not used as the Doris engine-version oracle.

Early `SHOW DATA` showed zero rows/bytes despite successful exact full reads.
It later reported the complete million rows: asynchronous catalog statistics are
not an independent correctness oracle. Directory allocation includes native
preallocation and unrelated metadata. A matched native compressed/uncompressed
payload pair was not qualified, so compression ratio remains **unavailable**.

Kubelet CPU rates use the native CPU timestamp, suppressing repeated cached
samples and recording counter resets. Current-container cgroups include CPU,
charged-memory peak, disk read/write bytes and read/write operation counts.
They are lifetime diagnostics, not complete phase costs across the killed
incarnation. Raw network counters remain saved but unaggregated. Short-lived Go
accounting, full build/import costs and device service latency are incomplete;
no holistic performance winner is claimed. Fresh Go processes do not evict
native/page caches; cold-read qualification remains unrun. All workers share one Docker VM.

Two failed setup attempts remain preserved: missing foreign-platform content in
the normal kind import, and Pod-ready BEs without native usable backend capacity.
Native-platform import and a bounded capacity gate corrected these without
resetting storage or pre-seeding data. Existing VM `max_map_count=262144` was read
back without alteration; this small fixture does not qualify production kernel
sizing. Scaling and deeper failure cases remain explicitly unrun above.

[Operator source](https://github.com/apache/doris-operator/tree/26.0.1)
· [Native Pod entry points](https://github.com/apache/doris-operator/blob/26.0.1/pkg/common/utils/resource/pod.go)
· [CR/PVC API](https://github.com/apache/doris-operator/blob/26.0.1/api/doris/v1/types.go)
