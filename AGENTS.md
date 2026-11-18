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

### Application code and Dockerfiles

Each runnable example must be **self-contained and reproducible**. Keep its
application entry points, Dockerfiles, configuration, dependency definitions and
test controllers accessible inside that example's directory. Running it must not
require executing a sibling example's Makefile or using its generated files or
previous state. Pin external images/charts and generate local artifacts through
the example's own lifecycle commands.

Use meaningful names such as `orders-workload/`, `telemetry-workload/`,
`producer/` or `consumer/`; do not call workload directories `client/`, `clients/`
or `shared/` without explaining their actual role. Keep application and controller
responsibilities clear; do not build a repository-wide workload framework merely
to remove duplication.

Keep service image Dockerfiles in `docker/` with names such as `Dockerfile.fe` and
`Dockerfile.be`; keep the application's Dockerfile beside its source. Avoid vague
names such as `runtime/` for a directory that only contains Dockerfiles. Deployment
configuration and test controllers belong beside the example that uses them.
Shared workload source may be exposed through a meaningfully named symlink inside
the example. Its target must be tracked in this repository and available in a clean
checkout; never depend on machine-local files. Before building, the example's Make
target materializes that source as regular files (hard links where supported, copies
otherwise) into a local `.build/` Docker context. Docker does not do this conversion
automatically. Commit the source, symlink and preparation script; **never commit the
materialized files**. Ignore `.build/` and verify this with `git check-ignore` and
the staged diff. Document the link and materialization in the example README.
Each example owns its cluster, context, state, lifecycle and results.

For Docker Compose examples, normally declare the workload applications (producer,
consumer, live writer/reader and verifier as appropriate) as services in that
example's Compose file. Build them from its local source with a Dockerfile and
invoke them through Compose from Make; one-shot tools may use a Compose profile.
Give services descriptive roles instead of requiring a separately managed client
container or host-installed application toolchain. For Kubernetes, deploy the
equivalent example-owned workload Pods/Jobs and keep their source/build local.

## Makefiles and local workflows

Prefer a small Makefile as the documented entry point for a runnable deployment or test. Use it to make common lifecycle actions repeatable (`up`, `test`, `status`, `cli`, `down`) and to expose relevant scenario-specific actions such as `failover`, `scale-out`, `scale-in`, or `benchmark`. A Make target should invoke the real underlying tools and report failures; do not claim a workflow works unless it has been run or otherwise verified.

Every runnable example README must begin its Quick start with a numbered, copy-pasteable lifecycle sequence. For a kind example, use `make kind-up`, `make up`, `make test`, `make down`, then `make kind-down`. For a Docker Compose example, use `make docker-up`, `make up`, `make test`, `make down`, then `make docker-down`. Put one command per numbered step and briefly say what it does. `kind-up`/`docker-up` prepares the cluster or service infrastructure; `up` installs or configures the example; `down` removes the example from that infrastructure; `kind-down`/`docker-down` destroys the infrastructure and its example-owned state. Keep additional scenario commands in a separate section.

Each Kubernetes example owns a separate kind cluster and context. Never share a
kind cluster between different systems or examples, and never use an unrelated
existing cluster. `make kind-up` creates that example's cluster/context in its own
kubeconfig; `make kind-down` deletes both the cluster and its owned context/config.
Re-running the same example may reuse its own still-running cluster.

For kind examples, name the cluster `<system>-<example>` and always pass its full context, `kind-<system>-<example>`, to every `kubectl` command (for example, `kubectl --context kind-kafka-strimzi-jbod ...`). Put the explicit context in the shared `KUBECTL` Make variable and use it in every recipe; direct `kubectl` calls must also include `--context`. Helm commands must use `--kube-context kind-<system>-<example>`. Never rely on or change the user's current Kubernetes context. Export a lab-specific `KUBECONFIG` before creating kind, so kind itself cannot overwrite the user's default context; retain explicit contexts on every kubectl/Helm command.

Examples commonly use Docker Compose for single-node and local cluster setups, and kind plus an operator, Helm chart, or StatefulSet for Kubernetes scenarios. Keep deployment files and scripts beside the example that uses them. Give Compose projects and resources clear, example-specific names, document ports and prerequisites, and make cleanup limited to resources created by that example. See `FORMAT.md` for the detailed requirements.

## Non-functional / production tests

For **all existing and future examples**, deliberately try to break the system in
the isolated lab. The goal is to understand its failure boundaries, symptoms,
availability and data-loss behavior, then demonstrate how to recover it. Include
Kubernetes operational failures, not just application queries. "Production-grade
testing" means exercising and measuring these boundaries; deploying several
replicas or passing a health check does not establish production readiness.
Start with individual failures, then test controlled combinations beyond the
documented fault tolerance where practical. Expected unavailability is a valid
observation; unexpected corruption or loss must remain a failed result. Document
the diagnosis, exact recovery procedure, required fencing/backups, operator/manual
steps and any unrecoverable state. Never conceal a broken run by resetting data.

Implement operational tests alongside functional correctness tests. Use the common
requirements below, the deployment-specific matrix, and the applicable workload
tests. These are requirements for new or materially changed examples, not claims
that older examples already pass them. When revisiting an older example, audit its
coverage and document gaps. A target name or an old README result alone is not
evidence of an executed test.

Keep the five-command Quick start. Expose independent, repeatable Make targets
for operational cases (`failover`, `scale-out`, `scale-in`, `rolling-restart`,
`disk-failure`, etc.). Document a serial `test-production` target that runs the
applicable matrix and exits nonzero on failure. Do not run destructive cases in
parallel or hide them inside a benchmark. List unsupported, blocked and unrun
cases explicitly, with version-specific upstream reasons for unsupported cases.

### Core test components

Build the production tests around these reusable responsibilities, using the
smallest implementation that fits the example:

- **Live data generator:** keep application writers **and readers** running while
  the database is changed. Generate representative operations with identifiable
  keys/versions and known expected values. Run readers independently so a blocked
  write does not stop observation of read availability or stale results. Keep both
  running before injection, throughout the operation and native recovery, and
  afterward to prove service has recovered.
- **Durable witness:** record attempted, successful, failed and ambiguous writes,
  plus acknowledged IDs/versions, outside the system and failure domain being
  tested. Preserve it through all fault and recovery phases.
- **Operation/fault controller:** perform node/process crashes, network failures,
  scaling out/in and up/down, rolling changes and recovery through supported native
  tools or deployment APIs. Emit timestamped phase markers so workload observations
  can be attributed to the actual database operation.
- **Behavior recorder:** continuously track successful/failed writes and reads,
  timeouts, retries, ambiguous outcomes, throughput and latency distributions.
  Record visibility lag, stale/wrong reads, backlog and native membership/health
  where relevant. Keep before/during/recovery/after phases separate; retain raw
  events or bounded histograms with enough counts to interpret percentiles.
  Report attempted/successful/failed operation counts and rates, plus p50/p99
  latencies separately for reads and writes and for successful and failed requests;
  fast failures must not make successful-request latency appear better.
- **Reconciler and report:** compare observed data with the witness, check shard
  placement and restored replication, and report numeric outcomes and limits.
  Account for the configured consistency model: a stale asynchronous read is an
  observation to quantify, while a violation of the promised contract must fail.

These components form one experiment: establish live progress, apply an operation
without pausing the workload, observe degradation and recovery, then reconcile.
Use separate processes or independent tasks for writers/readers/controller as
needed; do not build a generic framework merely to share a few lines. A final
successful read or a completed load generator cannot substitute for continuous
observations during the operation.

### Evidence required for every operational test

- Start from verified native membership, roles, replication and shard placement.
  Discover the actual leader/primary or shard owner instead of killing an arbitrary
  idle node. Exercise each distinct role and shard/failure domain where applicable.
- Keep a representative read/write workload running before injection, throughout
  the outage and until native repair finishes. Require observed progress before
  injection and prove the client did not exit or exhaust its input during recovery.
  Include writes while degraded when the consistency contract permits them.
- Record unique operation IDs, attempted operations, successful acknowledgements,
  errors, timeouts, retries and ambiguous commits in an external durable journal
  outside the failed storage/domain. Fsync the witness; preserve earlier test
  histories. Retry mutations only with a justified idempotency contract, and report
  retry masking separately from first-attempt errors.
  An error status, including a conditional-write failure, does not by itself
  prove no replica applied the operation. Retain its intent and reconcile possible
  effects against the system's actual contract; an observed applied body does not
  turn a failed/ambiguous response into an acknowledged write.
- Reconcile every acknowledged key and its expected body/version after recovery,
  including earlier phases. Check missing, duplicate, corrupt, stale and unexpected
  records; totals alone are insufficient. For sharding, verify routing and native
  ownership per shard and replica contents where the system exposes them. State
  when only configured placement or logical readback was checked.
- Prove the fault occurred: native down/offline state, exit signal, changed process
  or Pod UID, device removal, failed I/O, or rejected network traffic. Check that
  unrelated survivors did not restart. A pod deletion, read-only directory, lost
  volume and physical power failure are different experiments; name them precisely.
  Failed inspection is unknown state, not proof of process exit. Before replacing
  storage, fail closed on Docker/CRI/transport errors. If old container metadata is
  gone, require a healthy native runtime listing that proves its exact ID is absent;
  never interpret a nonzero inspection command as successful fencing.
- Wait for native catch-up, log replay, repair, rebalancing and restored redundancy,
  as applicable. A Ready pod, successful survivor read or desired replica count is
  not enough. Verify the repaired member serves data; where supported, fail a
  different member afterward to prove the recovered copy is usable.
- Assert both availability and expected rejection: minority/quorum loss, stale
  primaries and read-only replicas must obey the documented consistency contract.
  Never lower replication, disable fsync or weaken consistency to obtain a pass.
  Quantify any data loss allowed by asynchronous replication or snapshot recovery;
  loss beyond the stated durability contract fails the test.
- Use bounded waits, propagate client/background-job failures, and save diagnostics
  on timeout. Restore injected faults on failure without deleting the evidence.
  Verify project/cluster labels and exact owned devices before destructive actions;
  never fill the shared Docker disk, reboot the host or touch another example.
- Save dated raw results under ignored `results/`. Report workload and acknowledged
  counts, loss/corruption counts, retries/errors, throughput and p50/p99 before,
  during and after the change. Measure first successful operation, longest outage,
  election, catch-up and full-repair times separately where observable. State polling
  resolution, deliberate hold time, resources, versions and failure boundaries.
  One-host Compose/kind replicas do not establish independent-host durability.

### Docker Compose cluster tests

| Test | What the example must exercise and verify |
|---|---|
| Graceful restart and cold reopen | Stop/start one member with its volume retained; verify persisted state, catch-up and continued service. Separately restart the complete stack and verify persistence without implying availability during a full shutdown. |
| Primary and replica crash | SIGKILL the actual primary/leader and, separately, a follower/storage worker. Hold the target down long enough to observe degraded operation, then restart with the same volume and verify election, client reconnection and native recovery. |
| Planned switchover | Use the native role-transfer procedure where supported; verify old-primary fencing, new routing and safe rejoin under load. Keep this separate from unplanned failover. |
| Horizontal growth and shrink | Add then remove members/shards through native membership APIs and Compose configuration. Verify real placement changes, drain all owned data before removal, and check routing and replicas after each step. Use supported counts, normally 3 → 5 → 3, with at most 7 nodes in scaling experiments. Do not confuse standbys with voting replicas or data replicas with shards. |
| Vertical resource change | Increase then reduce CPU/memory/cache settings within supported bounds; recreate members one at a time when needed. Verify effective limits/settings, quorum, working-set effects and performance after each change. |
| Rolling maintenance | Restart every member serially, waiting for native catch-up before the next. Test a pinned supported version upgrade when included; exercise rollback only if upstream supports it and record on-disk compatibility limits. A same-version restart is not an upgrade test. |
| Replica delay and network partition | Pause/delay one replica and separately isolate its peer traffic using project-scoped rules. Verify lag/backpressure, majority/minority behavior, stale-read policy, fencing and convergence after healing. Restore rules and unpause in cleanup. |
| Disk failure and replacement | Separately test retained-volume process recovery, injected write failure and permanent loss of one owned data volume where applicable. Fence/stop the old owner, provision an empty replacement using the native recovery procedure, and verify rebuilt redundancy, not just reads from surviving copies. |
| Dependency and role outages | Fail metadata/consensus, router, gateway, catalog, object storage and WAL services separately when present. Verify which operations remain available, how clients reconnect and that no acknowledged state disappears after dependency recovery. |
| Resource exhaustion | Use bounded, isolated quotas/devices to exercise disk-full, low-memory/OOM or CPU throttling where applicable. Verify explicit errors/backpressure, retained committed state and recovery after restoring capacity. Never consume all shared host capacity. |
| Lifecycle and ownership | Run the full five-command lifecycle, repeat setup/configuration and restart from retained state. Verify `down` removes workload state as documented and `docker-down` removes only owned containers, networks, volumes and built images; preserve external journals and unrelated resources. |

### Kubernetes operator tests

Apply the common evidence rules and relevant database failure cases above, but
exercise the operator's declarative API and Kubernetes failure handling. For a
Helm/StatefulSet fallback, test its actual deployment mechanism and mark
operator-only reconciliation tests unsupported; do not call it an operator.
Every case uses the example's private kubeconfig and explicit full context.

| Test | What the example must exercise and verify |
|---|---|
| Bootstrap and convergence | Install pinned CRDs/operator/chart and create the CR from an empty owned cluster. Wait for controller readiness, the latest observed generation where exposed, per-role readiness and native database health/placement. Reapply the same desired state and verify it converges without unnecessary disruption. |
| Scale out and scale in | Change supported member/shard/role counts in the CR, not just a generated StatefulSet. Verify operator reconciliation, native membership, balanced placement and safe draining before removal. Test storage and compute roles separately when independently scalable; retain data checks throughout both directions. |
| Scale up and scale down | Change supported CPU/memory/cache settings through the CR or documented chart API. Verify effective pod resources and native settings, serial rollouts, quorum and post-change performance. Distinguish vertical resizing from examples whose older `scale-up` target actually adds shards. |
| Graceful rolling restart | Exercise the supported restart/configuration mechanism across all members. Check replacement identity, per-member catch-up, readiness probes, termination behavior and quorum before proceeding to the next member. |
| Leader and follower crash | Separately force a real process/container crash and test Pod recreation. Discover roles natively, prove the old process terminated, observe changed container ID/restart count or Pod UID as appropriate, and wait for election and repair. Force-deleting a Pod object alone does not prove the old process was fenced. |
| Worker-node outage and restart | Stop and restart one label-verified, example-owned kind worker while load runs. Record affected pods, native failure detection, recovery and data correctness. Keep the control plane and workload witness available; distinguish node restart with retained storage from lost-node/disk replacement. Never reboot the host. |
| Drain and reschedule | Cordon/drain one owned worker using normal eviction semantics; verify PodDisruptionBudget behavior, placement/anti-affinity and native quorum. Demonstrate an unsafe voluntary eviction is blocked when a PDB is configured. Explain local-PV pinning or insufficient spare capacity rather than bypassing safeguards. Uncordon afterward. |
| PVC persistence and empty-disk recovery | Recreate a member with the same claim, then separately replace one safely fenced, owned claim/device using the supported operator procedure. Verify retained versus new volume identity, actual native rebuild and restored copies. Distinguish local cache loss from durable WAL/object-store loss. Test expansion only where the StorageClass and operator support it; do not imply PVC shrink support. |
| Operator outage and restart | Stop/crash the operator independently of database pods. Verify the existing data plane's behavior, demonstrate reconciliation is unavailable while the controller is down, then restore it and verify pending desired changes converge without unintended data-plane restarts or data loss. |
| Reconciliation and invalid configuration | Make a supported, reversible child-resource drift and verify restoration from the CR where the operator owns that field. Test invalid/unsafe CR changes, admission or controller errors and recovery to valid desired state. Do not edit generated resources as the normal configuration path or assume every field is operator-managed. |
| Network and dependency outages | Inject namespace/worker-scoped failures for database peers, DNS or external WAL/S3/catalog dependencies as applicable. Prove the installed networking actually enforces the fault; a NetworkPolicy manifest alone is insufficient. Check quorum, retry behavior, healing and controller/data-plane convergence. |
| Resource pressure and scheduling | Exercise bounded container OOM/throttling and an unschedulable requested resource change where applicable. Verify useful CR conditions/events, probe behavior, preserved data and reconciliation after resources are restored. Keep the kind control plane and host healthy. |
| Upgrade and recovery | Where supported, test a pinned operator/CRD/database upgrade path under load, including conversion/compatibility and roll sequencing. Test backups/restores through the operator's supported API if supplied; retain an independent restore oracle. Mark untested rollback paths explicitly. |
| Teardown and recreation | Run `down`, then `kind-down`; check workload removal, supported finalizer completion, cluster deletion and removal of the owned context/private kubeconfig. Recreate cleanly. Never force-remove finalizers or delete unrelated contexts to make cleanup pass. |

### Workload-specific production tests

Select the rows that match the system's advertised workload; explain omissions.
These extend both deployment matrices instead of creating duplicate examples.

| Capability | Required operational checks when applicable |
|---|---|
| Sharded databases | Verify every customer's expected shard and native replica placement before/after resharding, failover and repair. Exercise each shard, routing refresh, hot-key/skew effects and cross-shard transaction boundaries; detect duplicate/missing rows during moves. |
| Replicated databases and caches | Test native consistency levels, stale-reader behavior, primary fencing, replica catch-up and loss of quorum. Distinguish synchronous durability, asynchronous RPO and snapshot-only persistence, including writes after the last snapshot. |
| Messaging and stream processing | Keep producers/consumers live through broker/controller/connector failure. Verify message IDs, partition ordering where guaranteed, duplicates, consumer offsets, backlog/lag and final catch-up. Test replica/disk reassignment before disk removal; verify replication checkpoints and consumer-then-producer cutover for cross-cluster examples. |
| S3 and distributed filesystems | Test data, metadata/control and gateway failures independently; verify full object bodies/checksums, multipart/range/version behavior where used, native replica/erasure placement and repair completion. Include one empty-device replacement and prove the rebuilt copy persists across restart. |
| Snapshots, backups and disaster recovery | Restore into separate owned storage and verify complete data, not only backup creation. Exercise interrupted incremental transfer/resume, read-only validation, source fencing before promotion, divergent-tail handling and quantified RPO. A same-disk snapshot is not an independent backup. |
| Lakehouse, archives and TTL | Interrupt before export, after export and during verification/commit; verify atomic visibility, retry/idempotency, restart/resume and retention of source data until the archive is verified. Test compaction against the same logical snapshot, concurrent readers and safe cleanup/retention boundaries. |
| Search and vector indexes | Keep updates and searches running through replica/shard failure; verify acknowledged documents and versions, tenant/filter isolation, index visibility lag, query correctness/recall and convergence after repair. Separate native sharding from application-level fan-out. |
| Metrics, logs and traces | Check timestamp/value/body fidelity and expected cardinality, ingestion backpressure, query freshness, retention and recovery across WAL/object-store outages. Validate each advertised signal; metrics/logs tests do not establish tracing support. |

### Performance and other production properties

- Run correctness and recovery checks before performance tests. Measure baseline,
  degraded operation, repair/rebalance and recovered state at stated offered load,
  concurrency, batch sizes, data sizes and resource caps. Include saturation and
  bounded soak tests for relevant systems; report queue growth, lag, memory/disk
  growth and failures, not only successful-request throughput.
- Separate API admission latency from durable commit, query visibility and
  end-to-end latency. State cold/warm caches, compression, retry policy, emulation,
  shared-host contention and run count; do not call a rate-limited fault workload
  or a tiny correctness fixture a capacity benchmark.
- Compare systems on the same machine class: CPU model/architecture and count,
  RAM, storage type/capacity/IOPS limits and network settings. Match per-node
  resources for comparable roles and report the whole-stack budget, including
  WAL, metadata, routers, collectors and object storage. Use the same workload
  generator budget; state topology/durability differences instead of hiding costs.
- Exercise **different insert/query approaches** where supported. Compare
  **single-record inserts versus batched inserts**, plus appropriate native bulk
  APIs, prepared/pipelined requests or alternative query forms. Sweep matching
  batch row/byte sizes, concurrency and compression; keep data, results,
  transaction/durability semantics and retry rules equivalent. Report each path
  separately with per-record and per-request rates; do not select one approach
  for one system and a different approach for its competitor without labeling it.
- Measure **cold versus warm queries separately** on the same dataset and query
  semantics. Document warmup count and which caches are cold/warm: result, plan,
  engine block and OS page caches where applicable. Restarting an application does
  not establish a cold OS cache. Use supported cache controls or an isolated
  owned VM for cold runs, never clear a shared host's caches. Validate returned
  bodies/results in both modes and report cache preparation separately.
- Publish the **dataset shape**, not only a row count: exact input bytes with
  explicit MB/MiB units, columns/types, distinct keys/tenants/groups, nulls and
  skew, generation seed or corpus revision, and artifact checksums. Separate input
  size, native persisted size, process memory and whole-stack memory; a small
  numeric synthetic fixture does not qualify string-heavy or skewed workloads.
- For **cross-language comparisons**, keep the native engine version/build and
  query/output semantics identical; inspect bindings for bundled engine revisions.
  State whether each driver uses an official binding, a common C ABI adapter or
  RPC. Identify the timed boundary: parsing/planning, execution, fetching/conversion,
  language-side validation and logging. An FFI benchmark does not compare native
  language implementations or establish that one language makes an engine faster.
- Record **CPU and memory on both competitors**. Measure wall time and process
  user+system CPU across all engine threads, stating CPU core-seconds versus
  utilization percentages. Record process peak RSS and container/cgroup peak
  memory where available; distinguish heap from charged file cache/kernel memory.
  Include import/setup as well as query/recovery costs. Save the sampling cadence
  and exact counters: fast operations can finish between sampled observations.
- Use at least **three fresh-process trials** for comparative performance claims,
  alternate or randomize system order, and retain per-trial counts and samples.
  State warmup count and whether a summary is pooled or a median of trial summaries.
  Report percentile sample sizes; a p99 from a handful of requests is effectively
  a maximum, not established tail behavior. State benchmark rates' actual timed
  scope; reciprocal latency is not measured end-to-end throughput. Report winners
  per workload and resource cost rather than declaring a universal winner.
- Validate complete query outputs against an **independent oracle**, including
  group keys, ordering, joins and numeric/null semantics; matching totals alone
  can conceal wrong groups or misplaced data. Add focused negative controls for
  false-success risks such as malformed replies, incorrect expected results and
  invalid/null values. Document adapter restrictions and prove numeric casts fit
  their target range instead of silently accepting truncation or overflow.
- Verify the health/metrics/logs used to diagnose each test expose the injected
  failure, lag and recovery. Save native status, controller events and relevant
  logs alongside client measurements; a dashboard alone does not validate data.
- Where the example configures authentication, authorization, TLS or tenant
  isolation, test allowed and denied access, bad credentials/certificates and
  relevant credential rotation. Label plaintext/local credentials as lab limits;
  do not infer production security from a private Docker network.
- Record exact passed, failed, blocked, unsupported and not-run cases in each
  README with numeric outcomes and limitations. Actually execute applicable
  operational targets before claiming production-test coverage. Adding this guide
  does not retroactively qualify any system or replace missing experiments.

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

Keep durable conventions in this guide, measured results in each example's README,
and current gaps/next steps in `TODO.md` or `PRODUCTION_TEST_AUDIT.md`. Distinguish
unfinished implementation, compiled/unit-tested code and actually executed live
experiments; preserve failed outcomes and identify the tested revision/run.

## Technology research

Before adding or materially changing an example for a technology, and before describing what it is good or bad at, read the relevant official manpages and inspect the corresponding source code. Use the documentation and source for the version actually used by the example when possible. Follow the execution path relevant to the example (configuration, query or API behavior, storage, replication, scheduling, or failure handling) rather than relying only on a project overview or benchmark headline. Use this research to choose representative workloads and explain tradeoffs; distinguish behavior confirmed in source from behavior measured by this repository's example, and cite useful upstream references in the README.

## Programming language practices

- Do not submit the first generated draft. Make **2–3 implementation/review passes**:
  first implement the smallest working design; then review and rewrite for
  correctness, language idioms and system-specific best practices; finally remove
  unnecessary code and verify the final implementation. Read the complete changed
  code and its actual callers, not just the happy-path output.
- Avoid "AI slop": speculative abstractions, duplicated helpers, unnecessary
  dependencies, hand-written replacements for established functionality, broad
  exception swallowing, silent fallbacks and retries that conceal failures. Keep
  useful comments about invariants and non-obvious behavior; remove boilerplate
  explanations that merely restate the code. Handle partial failure and cleanup
  explicitly without converting a failed experiment into a successful one.
- Write example application/workload code generally in C++, Rust or Go. This
  includes producers, consumers and database clients that model the application
  and generate the measured workload. Pick the language and maintained client
  library that fit the system; explain any necessary exception in the README.
- Controller/testing code may use any suitable language, including Go or Python:
  infrastructure orchestration, fault injection, recovery coordination, test
  assertions, result collection and report generation need not use the same
  language as the application. Keep this boundary clear; a workload generator is
  application code even when invoked by `make test`.
- Follow the language's established idioms, standard library, and ecosystem conventions. Prefer standard-library facilities for common tasks; use a focused, mature library when the standard library does not provide the needed capability.
- Apply the database/system's own best practices as well as the language's. Read
  official guidance and pinned-version source, and use relevant available skills
  (for example, ClickHouse architecture and best-practices guidance when designing
  or changing ClickHouse schemas, queries or configuration). Check data modeling,
  batching, resource limits, consistency/durability, replication and recovery
  procedures against that guidance. Cite sources and explain deliberate lab
  deviations; do not replace native operational tools with invented equivalents.
- Do not hand-roll functionality that a standard library or suitable, maintained library already provides. Avoid adding a dependency when the standard library is sufficient, and avoid unnecessary dependencies that duplicate existing project capabilities.
- Use the normal build and dependency tools for each language (for example, CMake for C++, Cargo for Rust, and Go modules for Go). Do not introduce multiple build systems such as both Bazel and CMake without a concrete requirement.
- Keep dependencies and abstractions small and justified. Handle errors explicitly, respect resource and concurrency semantics, and use the official docs/source for the versions in use to confirm behavior.
- Run the language's formatter and appropriate compiler/static checks, then the
  meaningful correctness and operational tests against the final code/configuration.
  Record exactly which revision/setup was tested; an earlier draft's passing run
  does not validate later behavioral changes. Documentation-only edits need their
  relevant checks, not a fabricated claim that live experiments were repeated.

## Changes

Keep changes scoped to the relevant example and its indexes or shared conventions. Before editing, inspect neighboring examples and their Makefiles so new work fits the repository's actual patterns rather than imposing one layout on unrelated language projects.
