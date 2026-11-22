# Format for system examples

How to add and test a database, pub/sub or other system under `<system>/`. Follows what the
existing examples do (`ydb/`, `scylladb/`, `tigerbeetle/`, `oceanbase/`, `rondb/`, …).

Each experiment should teach **how the system fails and how to recover it**.
Investigate ordinary workload/operational failures as well as injected faults;
preserve triggers, symptoms, diagnosis, data/service impact and demonstrated
recovery limits. Follow the [production-test guidance](AGENTS.md#non-functional--production-tests)
and keep unresolved failures explicit alongside performance results.

## Layout

```
<system>/
  README.md                  # system index (see "Top-level README")
  single-node/               # minimal setup
  docker-compose-cluster/    # replicated / sharded cluster on Docker Compose, with failover and scaling
  kubernetes-operator/       # official operator on kind (or kubernetes-helm/, kubernetes-statefulset/), with scaling
  <workload-name>/           # a program on the system's strongest workload (payments-ledger/, market-data/)
```

| Folder | When | Branch name |
|---|---|---|
| `single-node/` | always, if the system can run as one process or container | `<system>-single-node` |
| `docker-compose-cluster/` | the system replicates or shards; includes `failover`, `scale-out`, `scale-in` | `<system>-docker-compose-cluster` |
| `kubernetes-operator/` | an official operator exists; includes `scale-out`/`scale-in` when the operator scales | `<system>-kubernetes-operator` |
| `kubernetes-helm/` | official Helm chart, no operator; same scaling rule | `<system>-kubernetes-helm` |
| `kubernetes-statefulset/` | neither exists; same scaling rule | `<system>-kubernetes` |
| workload folder | always: the workload the system was built for (see below) | `<system>-<workload>-showcase` |

- Folder and file names describe the workload. Never put `showcase` in a path (it is fine in a branch name).
- If an example is impossible (no OSS clustering, no single-process mode), do not fake it. Say
  why in `<system>/README.md`, with sources (see `skytable/`, `questdb/`, `rondb/`).

Each example folder:

| File | Purpose |
|---|---|
| `Makefile` | every command in the README; see "Make targets" |
| `docker-compose.yml` or `kind-config.yaml` + manifests / `values.yaml` | the deployment |
| `README.md` | see "Example README" |
| `.gitignore` | `results/`, image tarballs, license files, local state |
| `sql/`, `cql/`, `scripts/` | numbered files run by `make test` (`01-schema.sql`, `02-…`) |
| `bench/` | `run.sh`, `limits.sh`, optional `report.py` + `pyproject.toml` + `uv.lock` |
| `orders-workload/`, `telemetry-workload/`, `producer/`, etc. | meaningfully named workload source (Go, Rust or C++), with its Dockerfile |
| `docker/` | service image Dockerfiles, when an upstream image needs customization |

## Make targets

| Target | Required | Does |
|---|---|---|
| `up` | yes | start and **wait until healthy** (`docker compose up --detach --wait`, `kubectl wait`, `helm --wait`) |
| `test` | yes | run the walkthrough (`sql/*.sql` etc.) and exit non-zero on failure |
| `status` | yes | cluster/node state (`nodetool status`, `ndb_mgm -e show`, `kubectl get pods`) |
| `cli` | yes | interactive client inside the container |
| `down` | yes | remove **everything** this example created (below) |
| `failover` | clusters | stop a node under load, show the cluster keeps serving, restart it, show it rejoins |
| `benchmark` | when measured | `bench/run.sh` under `bench/limits.sh` caps; writes `results/` |
| `run` / `build` | workload folders | build the app image, run it against the running system |
| `scale-out` / `scale-in` | cluster / Kubernetes examples | add nodes, then remove them, with load running |
| `logs` | optional | follow logs |
| `kind-cluster`, `operator`, `cluster` | kind examples | steps of `up`, each idempotent |
| `kind-up` / `kind-down` | layered kind examples | create/delete the kind cluster separately from the example |
| `docker-up` / `docker-down` | layered Compose examples | start/fully remove Compose infrastructure separately from the example |
| `aio-max-nr` etc. | when a kernel setting is needed | prerequisite of `up` (see `oceanbase/*/Makefile`) |

### Quick-start lifecycle

Every runnable example README starts with a numbered, copy-pasteable sequence. For kind:

1. `make kind-up` — create the kind cluster.
2. `make up` — install/configure the example and wait until healthy.
3. `make test` — run the example's checks.
4. `make down` — remove the example resources, keeping kind available.
5. `make kind-down` — delete the kind cluster and its example-owned images/state.

For Docker Compose, use the same order with `docker-up` and `docker-down` replacing `kind-up` and
`kind-down`. `docker-up` starts the supporting Compose infrastructure; `up` provisions the example;
`down` removes the example layer; `docker-down` removes its Compose services and volumes. Put
scenario-specific operations in a separate README section. Keep existing examples as-is unless the
task asks to migrate them.

For kind, set `CLUSTER_NAME := <system>-<example>` and pass `--context kind-$(CLUSTER_NAME)` on
**every** `kubectl` invocation. The shared `KUBECTL` Make variable must include this context, and
direct `kubectl` calls in recipes must include it too. Helm invocations must include
`--kube-context kind-$(CLUSTER_NAME)`. Never rely on the user's current Kubernetes context or change
it; for example, a Kafka example uses `kind-kafka-strimzi-jbod` explicitly.

Layered examples preserve that boundary in the Makefile: kind `down` removes the example resources
and operator while keeping the kind cluster, and Compose `down` removes the example workload while
keeping its supporting services. The final `kind-down` or `docker-down` target removes the remaining
infrastructure and example-owned state. For a clean rerun, use the full five-command sequence in the
README.

### `make down`

Single-layer examples remove their entire deployment with `down`. Layered examples split workload
cleanup from infrastructure teardown:

| Deployment | Command |
|---|---|
| Compose, single-layer | `docker compose --profile <every profile> down --volumes --remove-orphans --rmi local` |
| Compose, layered | `down` removes the example workload; `docker-down` runs the full Compose cleanup above |
| kind, single-layer | `kind delete cluster --name $(CLUSTER_NAME)` (plus `docker image rm` of images built for it) |
| kind, layered | `down` removes example resources; `kind-down` deletes the kind cluster and example-built images |

- Never `docker system prune`, `docker volume prune` or remove images the example did not build.
- The README's full lifecycle sequence must start from and return to a clean state.

## Docker Compose

Keep current experiments [local and isolated](AGENTS.md#local-isolation-and-credentials):
explicit example-owned endpoints and lab-only credentials, no host AWS
configuration/credentials or real cloud resources, and no SDK fallback to public
cloud endpoints or metadata credentials. Use an internal Compose network where
practical. A local reference for a managed service is a different backend; name
and report it accurately.

Verify required storage semantics on the actual mounted filesystem. When a
database relies on file locks or atomic publication, test them across independent
processes/containers; do not infer support from a mount path or syscall success.
Compare a small standard-library control with the native database operation.
Keep a failing storage configuration reported when evaluating an alternative.
Host shares, named volumes and PVC storage may differ; a flush request is not
proof of physical power-loss durability.

After teardown, use healthy runtime listings to verify owned containers, networks
and volumes are absent, including profile-only/one-shot resources. Exit status 0
alone is insufficient: cleanup can warn that a resource is still in use. Export
failed state only after fencing its owner, and retain storage if export fails.

- `name: <system>-<example>` at the top: a unique project name, so examples run side by side.
- Container names prefixed with the system (`rondb-fs-ndbd-1`).
- Use the latest stable upstream release for new and revisited evaluations;
  verify official releases and supported engine/operator/client combinations.
  Pin exact versions and image digests, never a floating `latest` tag. Confirm
  the native engine and extension versions: a newer binding can bundle an older
  engine. Keep old versions only as labeled historical/regression/upgrade cases;
  recheck their failures on current stable builds. Document unavailable latest-
  version integrations and keep that coverage pending. See `AGENTS.md` for the
  stable-release and historical-data preservation rules.
- Host ports overridable: `"${KDB_PORT:-5102}:5000"`. Bind `127.0.0.1` when there is no auth.
- `healthcheck:` on every long-running service; `depends_on: { condition: service_healthy }`.
- One-shot init/setup services with `service_completed_successfully` (bucket creation, `sysctl`, bootstrap).
- `profiles:` for things `up` should not start: `tools`, `bench`, `run`.
- Kernel settings that are not namespaced (`fs.aio-max-nr`) are raised by a privileged one-shot
  (`scylladb/*/docker-compose.yml`) or a Makefile prerequisite (`oceanbase/*/Makefile`). Document
  that on Docker Desktop it changes the whole Docker VM until Docker restarts, and on Linux the host.

## kind (Kubernetes)

Each system/example gets its own kind cluster, context and kubeconfig. Do not
share clusters across different examples. `kind-up` creates them; `kind-down`
deletes the owned cluster and context/config. A rerun may reuse only that same
example's cluster. Keep deletion scoped to its configured name.

Export a lab-specific `KUBECONFIG` before invoking kind; cluster creation otherwise
changes the default context in the user's kubeconfig. Keep the explicit context on
every kubectl/Helm command even with this isolated file.

```make
CLUSTER_NAME := <system>-<example>
KUBECONFIG := $(CURDIR)/results/kubeconfig
export KUBECONFIG
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM    := helm --kube-context kind-$(CLUSTER_NAME)

kind-up:
	@mkdir -p results
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --name $(CLUSTER_NAME) --config kind-config.yaml
```

- `kind-config.yaml`: provide enough workers for member placement, worker restart and
  drain tests while retaining the control plane and workload witness. Use 3 workers,
  one per `topology.kubernetes.io/zone` label, for zone-aware placement. State that
  these labels do not create independent physical failure domains.
- Pin chart and operator versions (`--version`). `helm upgrade --install --wait --timeout`.
- Wait on the real readiness signal: `kubectl wait --for=jsonpath='{.status.state}'=Ready`.
- `kind load docker-image` fails for multi-arch images on Docker Desktop (`content digest not found`):
  side-load with `docker save --platform <arch> <img> | docker exec -i <node> ctr -n k8s.io images import --digests -`.
- Lowered resource floors (operator minimums, PVC sizes) go in the manifests with a comment saying
  what the default is and why it was lowered.
- `kind`, `kubectl`, `helm` come from the repo's dev shell (`nix develop` / direnv, `flake.nix`).

## Workload example (what the system is built for)

Every system gets at least one example of the workload it was purpose-built for and is fastest at
(TigerBeetle: payments ledger; QuestDB: market-data ticks; RonDB: online feature store; OceanBase:
HTAP orders). Run this workload **IN A CLUSTER WHERE POSSIBLE**, using Docker Compose or Kubernetes.
It may share an existing cluster example rather than duplicate deployment files.
Explain any single-node-only boundary. This is the example that shows why someone
would pick the system.

- Folder named after the workload (`payments-ledger/`, `market-data/`), program in a descriptive directory such as `orders-workload/`
  (Go, Rust or C++; official client library where one exists), built by a Dockerfile so the host needs
  only Docker. `make up`, `make run`, `make down`.
- Model a realistic workload, not a microbenchmark: real schema, mixed operations, concurrency.
- Compare with a relevant competing system on the same data and workload, with
  explicit resource budgets, durability settings and correctness checks. Explain
  semantic differences; distinguish measured comparisons from proposed ones. A
  naive baseline can supplement the competing system.
- Check correctness at the end (counts, sums, invariants) and exit non-zero if it fails.
- Print throughput and p50/p99 per phase; the README gets the table and a short "Design notes" list of
  the technical reasons (no sales pitch).

## Scaling

Not a separate example: scaling is part of the cluster example (`docker-compose-cluster/` with
`make scale-out` / `make scale-in`) and/or the Kubernetes example (operator, Helm or StatefulSet with
the same targets), whichever the system scales through. Say so in that example's README and in the
examples table of `<system>/README.md` (e.g. "3 nodes, failover, scale 3 → 5 → 3").
(`oceanbase/scale-out-in/`, `rondb/online-scaling/` and `tigerbeetle/cluster-operations/` are older
separate folders and stay as they are.)

- Grow, then shrink, with load running: 3 → 5 → 3 or 3 → 7 → 5. Never more than 7 nodes.
- Per step: the command, how long rebalancing took, throughput before/during/after, failed requests.
- Scale up (CPU/memory per node via rolling restart) if the system supports it.
- If scale-in or online node changes are not supported, say so with the exact error and a source.

## Failover

Follow [AGENTS.md production tests](AGENTS.md#non-functional--production-tests): shared evidence
requirements, separate Compose and Kubernetes operator matrices, workload-specific
cases and performance checks. Implement applicable cases as independent Make
targets and a documented serial `test-production` aggregate. Existing examples do
not become qualified merely because these requirements were added.

Also follow the [production deployment and ongoing operations guidance](AGENTS.md#production-deployment-and-ongoing-operations):
explain the deployment architecture and exercise applicable compaction/background
maintenance, node replacement, long-term storage/tiering, retention/TTL and routine
capacity operations under live load. Document configuration, measured effects,
safe procedures and local-lab limits in the example README.

Work across systems in [two or three testing passes](AGENTS.md#progressive-testing-across-systems):
survey the agreed scope first, establish runnable correctness/recovery coverage,
then return for deeper production tests. Prioritize sustained performance
comparisons for stable evaluated configurations that pass their relevant gates.

- Keep load running through the outage and native repair. Distinguish graceful
  restart, crash, node outage, network partition and permanent disk loss.
- Reconcile external acknowledged IDs and complete expected values/versions;
  counts alone cannot prove no loss. Assert the configured durability contract,
  including quantified asynchronous/snapshot RPO where applicable.
- Report first successful operation, longest outage, errors/retries, election,
  catch-up and restored redundancy separately. Pod readiness alone is insufficient.
- Verify effective native cache, durability and other declared tunables before
  and after recovery, including settled post-boot values. Persist them through
  supported startup configuration. A surviving container memory limit does not
  establish that an engine or filesystem cache cap survived. Record unexpected
  configuration drift separately from data correctness and rerun after fixing it.

## Benchmarks

- Independent functional/recovery examples may run in parallel within a verified
  combined resource budget, with separate owned infrastructure and histories.
  Keep each example's fault cases serial. Isolate comparative benchmark trials
  from competing jobs on the same host; label shared-host functional timings as
  diagnostics and record actual co-tenancy. See the
  [execution rules](AGENTS.md#progressive-testing-across-systems).
- Follow the [evaluation stages and test concepts](AGENTS.md#evaluation-stages-and-backend-contract)
  and [mandatory measurements for every experiment](AGENTS.md#mandatory-measurements-for-every-experiment).
  These apply to single-system, correctness, fault/recovery and comparative runs.
  Report CPU utilization/core-seconds, memory average/peak, measured compression,
  disk baseline/peak/settled bytes, I/O and network alongside operation metrics.
  Include workload and dependencies as well as database roles; distinguish limits
  from measured use and whole-stack space amplification from compression ratio.
- Performance benchmarks must explicitly report disk read/write bytes and rates,
  read/write IOPS and I/O latency/queue depth where available. Distinguish disk
  space from disk activity and logical/cached I/O from physical/device I/O; state
  counter scope, sampling interval and unavailable measurements.
- `bench/limits.sh apply CPUS MEM SERVICE…` caps containers with `docker update`; `restore` undoes it.
  Put the cap in every result.
- Short/single runs are diagnostics, labeled as such. Comparative claims require
  at least three independent trials, alternating system order and reporting median,
  min/max and dispersion. Start with 60s warmup and 5-minute baseline / 15-minute
  distributed measurement windows; explain deviations. Separate cold/warm runs.
- Use the system's own tool where it exists (`tigerbeetle benchmark`, `sky-bench`, `cassandra-stress`,
  YDB CLI workloads), otherwise sysbench, pgbench, go-tpc, go-ycsb or wrk.
- Every report states date, actual hardware/architecture, runtime/kernel, image
  digests, resource/storage/network budgets, topology/co-tenancy, source revision,
  seed, dataset shape/bytes and pinned client configuration. Do not assume the host
  matches an earlier experiment. Retain a configuration hash and phase timestamps.
- Images run under emulation (amd64 on arm64) must be marked as such.
- Raw output goes to `results/` (gitignored); the README gets the table.
- Commit compact curated result summaries, with per-trial metrics and provenance;
  keep raw logs, datasets, native database files and full inspection dumps ignored.
  Check actual large files and the Git index before staging; see
  [result artifact hygiene](AGENTS.md#result-artifacts-and-git-hygiene).
- Keep raw operation histories, phase-aligned resource samples and every failed or
  interrupted trial. Missing measurements are unavailable/incomplete, never zero;
  unsupported measurements need a reason. Historical operation-only results need
  new measurements before they qualify as holistic benchmarks.
- Number-centric: report measured values with units (ops/s, rows/s, MiB/s, p50/p99/p99.9 ms, s to
  recover, records lost), in a table. A claim without a number behind it ("fast", "scales well",
  "low latency") does not go in. Comparisons state both numbers and the ratio (e.g. "p99 8.1 ms
  vs 39.5 ms, 4.9x"). Every number carries its unit, including the first one in a pair or range.
- Include measured time per operation in ms, µs or ns, not only throughput: state
  operation type, timing scope, mean/percentiles, sample count and timer precision.
  Separate batch duration and amortized per-record cost from actual record latency;
  reciprocal concurrent throughput is not an operation-latency measurement.
- Separate immediate read-after-write from historical persisted-data reads, and
  hot, partially resident/warm and verified cold reads. Report data age, working
  set, cache layers/hit rates, reader session/primary/replica and visibility lag;
  old data is not necessarily cold, and a cached read does not prove strong consistency.
  Separate new inserts/appends, hot-key overwrites and updates of older nonresident
  keys, plus first-write/startup versus sustained writes under flush/compaction.
  Define write temperature per engine and report each case's latency and resource/I/O
  costs separately. Include concurrent reads of both recent and historical data.

## Licensed systems

- Never commit a license. Read it from an env var (`KDB_LICENSE_B64`) or a gitignored `<system>/license/` file.
- `make up` refuses to start without one and says where to put it.
- The README opens with a `## License: required before running` section: what to sign up for and where the key goes.
- If it cannot be run, the README says "Not run" and lists what was verified (image builds, client compiles).

## Example README

```
# <System> — <example name>

One or two lines: what this deploys or measures.

## Quick start
1. `make kind-up` — create this example's isolated infrastructure.
2. `make up` — install/configure the example.
3. `make test` — run correctness checks.
4. `make down` — remove the example.
5. `make kind-down` — destroy its infrastructure/state.

For Compose use `docker-up` and `docker-down` in steps 1 and 5.
Put fault/scaling/benchmark commands in a separate section (real targets only).

## Setup              table: service, image:tag, port, role, limits
## What it does       table or bullets
## Results            dated setup; correctness/availability/recovery verdicts;
                      operation counts/rates/latencies AND CPU, memory,
                      compression, disk/IO/network measurements and gaps
## Known issues       bullets: error text → cause → workaround
## Links
```

## Top-level README (`<system>/README.md`)

- `Website:` and `GitHub:` links.
- Examples table: folder | what it shows.
- Cluster note if there is no cluster example (why, with sources).
- Benchmark summary table: example | date | setup | result, linking to each example's `#benchmark`.
- `## Known issues`: bugs, version caveats, instability. Say plainly if the project looks stale.

## Writing

- Precise and short. Tables for ports, targets, settings and results; bullets over paragraphs.
- No marketing ("blazing", "powerful", "seamless"), no "why X wins" pitches, no restating conclusions.
- Keep exact error strings, versions and numbers. Do not round or guess; if something was not run, say so.
- Do 2-3 editing passes before committing: (1) check every number, command, port and link against
  what actually ran and the real files; (2) cut filler, repetition, hedging and marketing; (3) turn
  remaining prose into tables or short bullets where it reads better.

## Checklist for a new system

1. Branch per example from `main` (names above). Commit title `[<System>] <Example> - Docker|kind|Kubernetes`.
2. `make up && make test && make down` from a clean state; then `failover`, `benchmark`, `scale-*` where present.
3. `docker ps -a`, `docker volume ls`, `kind get clusters` show nothing left from the example.
4. `<system>/README.md` updated (examples table, benchmark summary, known issues).
5. Row added to the `## Systems` table in the root [`README.md`](README.md); item ticked in [`TODO.md`](TODO.md).
