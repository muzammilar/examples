# Repository guide

This repository is a collection of runnable examples, grouped first by technology or system and then by a concrete project or deployment shape. Read the nearest `README.md`, `Makefile`, and existing examples before changing a directory; preserve established local conventions and commands.

## Commit messages

Every commit title must use this format:

```text
[SYSTEM] Short Commit Message
```

`SYSTEM` is a placeholder: replace it with the uppercase technology or change category. Never use the literal `[SYSTEM]` prefix. Keep the summary short and descriptive.

Examples:

- `[KAFKA] Add MirrorMaker architecture experiments`
- `[NEO4J] Add cluster recovery example`
- `[CLICKHOUSE] Add ingestion benchmark`
- `[DOCS] Clarify example conventions`
- `[FIX] Correct shared cleanup behavior`

## Repository layout

- The root `README.md` indexes the language examples and supported systems.
- System directories (such as `clickhouse/`, `redpanda/`, and `valkey/`) normally have a top-level `README.md` that introduces the system and links to its examples.
- System subdirectories are standalone scenarios: a minimal single-node setup, a cluster, a Kubernetes deployment, or a workload-specific program. Names should describe what the example demonstrates.
- Language directories (`go/`, `python/`, `js/`, `rust/`, `cpp/`, `erlang/`) contain independent code samples and projects; they do not need to follow the database system layout.
- There are also focused infrastructure and learning collections such as `k8s/`, `vagrant/`, `ansible/`, and `iceberg/`.

## System examples

For new database, messaging, and infrastructure system examples, follow [`FORMAT.md`](FORMAT.md). It is the detailed source of truth for directory structure, Make targets, Docker Compose and kind conventions, workload examples, scaling, failover, benchmarks, README contents, and cleanup.

The typical system has a root README and focused subdirectories such as:

- `single-node/` for the smallest useful setup;
- `docker-compose-cluster/` for a replicated or sharded local cluster when supported;
- `kubernetes-operator/`, `kubernetes-helm/`, or `kubernetes-statefulset/` for Kubernetes deployment;
- a workload-named example showing the system's intended strength, when practical.

Every new system should cover its supported deployment shapes: single node,
Docker Compose cluster, Kubernetes operator (or Helm/StatefulSet when no suitable
operator exists), and a representative workload **IN A CLUSTER WHERE POSSIBLE**.
The workload may live in an existing cluster example instead of duplicating its
infrastructure. Explain unsupported modes with upstream sources.

Compare that workload with relevant competing systems on the same input data,
queries, correctness rules, durability settings and stated resource budgets.
Report measured throughput, latency, recovery and acknowledged data loss; identify
semantic differences and unrun comparisons. A language preference, including Rust,
is a reason to investigate a system, not evidence of performance or reliability.

Do not create every scenario mechanically. Follow the system's capabilities and explain unsupported or unavailable deployment modes in its README. Some systems and older examples use different but established layouts; maintain those unless the task is to migrate them.

## Makefiles and local workflows

Prefer a small Makefile as the documented entry point for a runnable deployment or test. Use it to make common lifecycle actions repeatable (`up`, `test`, `status`, `cli`, `down`) and to expose relevant scenario-specific actions such as `failover`, `scale-out`, `scale-in`, or `benchmark`. A Make target should invoke the real underlying tools and report failures; do not claim a workflow works unless it has been run or otherwise verified.

Every runnable example README must begin its Quick start with a numbered, copy-pasteable lifecycle sequence. For a kind example, use `make kind-up`, `make up`, `make test`, `make down`, then `make kind-down`. For a Docker Compose example, use `make docker-up`, `make up`, `make test`, `make down`, then `make docker-down`. Put one command per numbered step and briefly say what it does. `kind-up`/`docker-up` prepares the cluster or service infrastructure; `up` installs or configures the example; `down` removes the example from that infrastructure; `kind-down`/`docker-down` destroys the infrastructure and its example-owned state. Keep additional scenario commands in a separate section.

For kind examples, name the cluster `<system>-<example>` and always pass its full context, `kind-<system>-<example>`, to every `kubectl` command (for example, `kubectl --context kind-kafka-strimzi-jbod ...`). Put the explicit context in the shared `KUBECTL` Make variable and use it in every recipe; direct `kubectl` calls must also include `--context`. Helm commands must use `--kube-context kind-<system>-<example>`. Never rely on or change the user's current Kubernetes context. Export a lab-specific `KUBECONFIG` before creating kind, so kind itself cannot overwrite the user's default context; retain explicit contexts on every kubectl/Helm command.

Examples commonly use Docker Compose for single-node and local cluster setups, and kind plus an operator, Helm chart, or StatefulSet for Kubernetes scenarios. Keep deployment files and scripts beside the example that uses them. Give Compose projects and resources clear, example-specific names, document ports and prerequisites, and make cleanup limited to resources created by that example. See `FORMAT.md` for the detailed requirements.

## Documentation

- Each runnable example should have its own README with purpose, prerequisites, exact commands, and relevant ports or access details.
- The system README should act as an index and summarize what each subdirectory demonstrates.
- Keep commands, versions, ports, results, and limitations aligned with the files and observed behavior. State clearly when a scenario has not been run or cannot be supported.
- Prefer concise, factual descriptions and tables for setup, targets, and measured results. Avoid unsupported performance or capability claims.
- Include concrete numeric outcomes in each runnable example README: workload size, observed input/output counts, and any meaningful totals or state changes. Label smoke-test counts as correctness checks, not throughput benchmarks; only report performance rates, durations, latency percentiles, or resource usage when they were measured with units and a stated setup.

For distributed storage examples, treat process crashes and permanent disk loss as
separate tests. Keep a workload running through recovery, record acknowledged
operations outside the failed system, reconcile complete data afterwards, and
verify native repair/placement completion. A successful read from a survivor does
not establish rebuilt redundancy. Document actual failure symptoms, recovery
commands, gotchas and untested limits; never turn an interrupted or failed run
into a passing result.

## Technology research

Before adding or materially changing an example for a technology, and before describing what it is good or bad at, read the relevant official manpages and inspect the corresponding source code. Use the documentation and source for the version actually used by the example when possible. Follow the execution path relevant to the example (configuration, query or API behavior, storage, replication, scheduling, or failure handling) rather than relying only on a project overview or benchmark headline. Use this research to choose representative workloads and explain tradeoffs; distinguish behavior confirmed in source from behavior measured by this repository's example, and cite useful upstream references in the README.

## Programming language practices

- Follow the language's established idioms, standard library, and ecosystem conventions. Prefer standard-library facilities for common tasks; use a focused, mature library when the standard library does not provide the needed capability.
- Do not hand-roll functionality that a standard library or suitable, maintained library already provides. Avoid adding a dependency when the standard library is sufficient, and avoid unnecessary dependencies that duplicate existing project capabilities.
- Use the normal build and dependency tools for each language (for example, CMake for C++, Cargo for Rust, and Go modules for Go). Do not introduce multiple build systems such as both Bazel and CMake without a concrete requirement.
- Keep dependencies and abstractions small and justified. Handle errors explicitly, respect resource and concurrency semantics, and use the official docs/source for the versions in use to confirm behavior.

## Changes

Keep changes scoped to the relevant example and its indexes or shared conventions. Before editing, inspect neighboring examples and their Makefiles so new work fits the repository's actual patterns rather than imposing one layout on unrelated language projects.
