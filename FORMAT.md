# Format for system examples

How to add and test a database, pub/sub or other system under `<system>/`. Follows what the
existing examples do (`ydb/`, `scylladb/`, `tigerbeetle/`, `oceanbase/`, `rondb/`, …).

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
| `app/` or `client/` | the workload program (Go, Rust or C++), built by a Dockerfile |

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
| `aio-max-nr` etc. | when a kernel setting is needed | prerequisite of `up` (see `oceanbase/*/Makefile`) |

### `make down`

Leaves nothing behind that the example created, and nothing else:

| Deployment | Command |
|---|---|
| Compose | `docker compose --profile <every profile> down --volumes --remove-orphans --rmi local` |
| kind | `kind delete cluster --name $(CLUSTER_NAME)` (plus `docker image rm` of images built for it) |

- Never `docker system prune`, `docker volume prune` or remove images the example did not build.
- `make down && make up` must start from a clean state.

## Docker Compose

- `name: <system>-<example>` at the top: a unique project name, so examples run side by side.
- Container names prefixed with the system (`rondb-fs-ndbd-1`).
- Pin every image tag (no `latest`); note why a version was chosen if not the newest.
- Host ports overridable: `"${KDB_PORT:-5102}:5000"`. Bind `127.0.0.1` when there is no auth.
- `healthcheck:` on every long-running service; `depends_on: { condition: service_healthy }`.
- One-shot init/setup services with `service_completed_successfully` (bucket creation, `sysctl`, bootstrap).
- `profiles:` for things `up` should not start: `tools`, `bench`, `run`.
- Kernel settings that are not namespaced (`fs.aio-max-nr`) are raised by a privileged one-shot
  (`scylladb/*/docker-compose.yml`) or a Makefile prerequisite (`oceanbase/*/Makefile`). Document
  that on Docker Desktop it changes the whole Docker VM until Docker restarts, and on Linux the host.

## kind (Kubernetes)

```make
CLUSTER_NAME := <system>-operator
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM    := helm --kube-context kind-$(CLUSTER_NAME)

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
```

- `kind-config.yaml`: one node is enough for most operators. Use 3 workers, one per
  `topology.kubernetes.io/zone` label, when the example needs zone-aware placement (`ydb/`, `tigerbeetle/`).
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
HTAP orders). This is the example that shows why someone would pick the system.

- Folder named after the workload (`payments-ledger/`, `market-data/`), program in `app/` or `client/`
  (Go, Rust or C++; official client library where one exists), built by a Dockerfile so the host needs
  only Docker. `make up`, `make run`, `make down`.
- Model a realistic workload, not a microbenchmark: real schema, mixed operations, concurrency.
- Compare against the naive approach or a system already in the repo on the same data (one query per
  round trip vs batched; row store vs column store; Kafka vs Redpanda).
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

- Run a load generator, stop one node (`docker stop` / `kubectl delete pod --force`).
- Report: time until writes resume, failed and retried requests, acknowledged writes lost (must be 0;
  check by counting), time for the node to rejoin.

## Benchmarks

- `bench/limits.sh apply CPUS MEM SERVICE…` caps containers with `docker update`; `restore` undoes it.
  Put the cap in every result.
- Keep runs short (seconds to a few minutes). One run is fine; say so. Note when the Docker VM was shared.
- Use the system's own tool where it exists (`tigerbeetle benchmark`, `sky-bench`, `cassandra-stress`,
  YDB CLI workloads), otherwise sysbench, pgbench, go-tpc, go-ycsb or wrk.
- Every result line states: date, `Apple M4 Pro, Docker VM aarch64`, caps, data size, threads/clients.
- Images run under emulation (amd64 on arm64) must be marked as such.
- Raw output goes to `results/` (gitignored); the README gets the table.

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
make up / make test / make failover / make benchmark / make down   (real targets only)

## Setup              table: service, image:tag, port, role, limits
## What it does       table or bullets
## Results            table; one line with date, hardware, caps
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

## Checklist for a new system

1. Branch per example from `main` (names above). Commit title `[<System>] <Example> - Docker|kind|Kubernetes`.
2. `make up && make test && make down` from a clean state; then `failover`, `benchmark`, `scale-*` where present.
3. `docker ps -a`, `docker volume ls`, `kind get clusters` show nothing left from the example.
4. `<system>/README.md` updated (examples table, benchmark summary, known issues).
5. Row added to the `## Systems` table in the root [`README.md`](README.md); item ticked in [`TODO.md`](TODO.md).
