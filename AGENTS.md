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

Keep the example root small: README, Makefile and clear subdirectories. Group
controllers in `scripts/`, tests in `tests/`, deployment files in `deploy/`,
curated results in `reports/`, and detailed notes in `docs/` by architecture or
workload. The root README must map each system/data path to its relevant files.
Do not accumulate experiment scripts, JSON reports and long investigation notes
as dozens of root files. Raw/generated evidence remains ignored.
Distinct projects or data paths belong in descriptive project subdirectories,
with their own README and entry points; keep each project's source, tests, notes
and reports together. Do not flatten unrelated projects into one scripts folder.
A single lab may share infrastructure across closely related subprojects only
when its README makes that ownership and lifecycle explicit. Independently
runnable examples must still own their state and be self-contained.

Keep code idiomatic and focused: each module should have a clear responsibility,
with descriptive names and small public interfaces. Prefer standard libraries and
maintained native tooling over custom wrappers. Review the implementation in two
or three passes for correctness, unnecessary complexity and layout. When moving
files, update Make targets, imports, Docker build contexts, source inventories and
documentation links together; verify them offline before declaring cleanup done.
Do not add compatibility shims or duplicate source trees to conceal broken paths.

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

When testing repeated setup or retained-state recovery, invoke repeated Make
commands separately. Make executes a target only once per invocation:
`make up test up` does not run setup twice. Preserve evidence of the second
invocation and verify existing acknowledged state instead of reloading it.

Each Kubernetes example owns a separate kind cluster and context. Never share a
kind cluster between different systems or examples, and never use an unrelated
existing cluster. `make kind-up` creates that example's cluster/context in its own
kubeconfig; `make kind-down` deletes both the cluster and its owned context/config.
Re-running the same example may reuse its own still-running cluster.

For kind examples, name the cluster `<system>-<example>` and always pass its full context, `kind-<system>-<example>`, to every `kubectl` command (for example, `kubectl --context kind-kafka-strimzi-jbod ...`). Put the explicit context in the shared `KUBECTL` Make variable and use it in every recipe; direct `kubectl` calls must also include `--context`. Helm commands must use `--kube-context kind-<system>-<example>`. Never rely on or change the user's current Kubernetes context. Export a lab-specific `KUBECONFIG` before creating kind, so kind itself cannot overwrite the user's default context; retain explicit contexts on every kubectl/Helm command.

Examples commonly use Docker Compose for single-node and local cluster setups, and kind plus an operator, Helm chart, or StatefulSet for Kubernetes scenarios. Keep deployment files and scripts beside the example that uses them. Give Compose projects and resources clear, example-specific names, document ports and prerequisites, and make cleanup limited to resources created by that example. See `FORMAT.md` for the detailed requirements.

### Local isolation and credentials

Run the current experiments in **local, isolated Docker/Docker Compose labs**.
Do not inspect, load, mount or use the user's real AWS configuration, credentials,
accounts or resources. Use explicit example-owned endpoints and lab-only static
credentials; disable SDK default credential discovery, metadata-service lookup and
fallback to public cloud endpoints. Keep workload and backend services on an
internal Compose network where practical, and fail closed on an unexpected
endpoint. Never substitute a real cloud bucket for a missing local dependency.
Managed services without a local server, including Tigris, remain documented and
unrun; a local S3-compatible reference is a different backend and must be labeled
as such. Any future managed-service run requires a separate explicit user request.

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

Understanding **how and why a system fails, and how to recover it**, is a primary
outcome of these experiments. Investigate failures during ordinary workloads and
operations as well as injected chaos: overload, stalls/deadlocks, exhausted
resources, configuration drift, dependency loss, interrupted maintenance and
incorrect or corrupt results. For each observed failure, preserve a reproducible
trigger, symptoms and detection signals, evidence of the mechanism, its effect on
data and service, and the verified automatic/manual recovery steps and limits.
Record uncertainty and unrecoverable states honestly; discovering a reproducible
failure is useful evidence even when a safe recovery has not yet been found.

Document **all measured outcomes, good and bad**. The goal is to understand systems,
including why an approach fails, not to produce a success-only collection. Report
successful workloads and recovery alongside wrong answers, outages, corruption,
unsupported semantics and rejected approaches. A zero exit status or healthy
service does not override a failed independent correctness check. State the tested
version, configuration, input, expected versus observed behavior, numeric impact,
reproducer, evidence and unresolved attribution. Strong negative conclusions are
appropriate when bounded by that evidence; do not soften a failure into a pass
or blame an unisolated component.

When replacing a failed connector, backend or architecture, **retain it in a
separately named directory beside the replacement**, including source, reproducers,
failed result summaries and limitations. Link both paths from their parent README
and explain why the default changed. Keep raw evidence under its original ignored
paths. A replacement's success never erases its predecessor's failures or proves
unrun recovery cases. For example, the pinned `clickhouse_scanner` returned wrong
answers despite successful queries; preserve its [failed qualification and
measured results](iceberg/multi-system/projects/duckdb-clickhouse/clickhouse-scanner/README.md)
alongside the HTTP path that replaced it. Do not delete the rejected path during
cleanup, or silently omit it from the reported conclusions.

Implement operational tests alongside functional correctness tests. Use the common
requirements below, the deployment-specific matrix, and the applicable workload
tests. These are requirements for new or materially changed examples, not claims
that older examples already pass them. When revisiting an older example, audit its
coverage and document gaps. A target name or an old README result alone is not
evidence of an executed test.

Keep the five-command Quick start. Expose independent, repeatable Make targets
for operational cases (`failover`, `scale-out`, `scale-in`, `rolling-restart`,
`disk-failure`, etc.). Document a serial `test-production` target that runs the
applicable matrix and exits nonzero on failure. Run destructive cases serially
within each example; independent examples may run concurrently under the resource
and isolation rules below. Do not hide faults inside a benchmark. List unsupported,
blocked and unrun cases explicitly, with version-specific upstream reasons for
unsupported cases.

### Production deployment and ongoing operations

Understanding **how to deploy and operate a system in production** is a primary
goal alongside failure modes and recovery. For each system, explain the intended
deployment architecture and exercise its supported ongoing operations in the
isolated lab. Cover role placement, failure domains, replication/durability,
persistent storage, resource sizing, observability and safe maintenance. Identify
which guarantees require independent hosts, disks or availability zones and what
the local example cannot establish. Base configuration choices on version-specific
upstream guidance and verify the effective configuration.

Include these cases where applicable, extending the existing example and test
matrices rather than creating duplicate infrastructure:

| Operation | What to demonstrate and measure |
|---|---|
| Compaction and background maintenance | Exercise native compaction, merges, checkpoints, vacuum or garbage collection under live reads/writes. Observe backlog, temporary space, CPU/memory, disk read/write bytes and I/O, amplification and foreground latency. Verify complete data after interruption, restart and completion. |
| Node failure and replacement | Demonstrate role discovery, fencing, failover, replacement/bootstrap, repair and restored redundancy using the supported deployment procedure. Keep clients live and reconcile acknowledged data; distinguish retained storage, permanent disk loss and whole-node loss. |
| Long-term storage and tiering | Exercise supported durable local/object storage, hot/cold tiers and archival with older data. Verify reads after eviction/restart, dependency loss and recovery, migration/retry safety, storage growth and retrieval costs. Distinguish caches, replicas, archives and independent backups. |
| Retention and TTL-based deletion | Verify expiry boundaries, mixed TTLs, concurrent updates/readers, restarts and documented clock semantics. Measure logical expiry separately from physical reclamation, tombstones and compaction lag. Test snapshot/backup/object-version retention and safe garbage collection; source data must survive until an archive is verified. |
| Routine operations and capacity | Exercise scaling, rolling maintenance, configuration changes and independent backup/restore as supported. Track capacity headroom and bounded sustained growth, maintenance interference and recovery time. Document actionable health/lag/space signals and the exact operator or manual runbook. |

Publish the commands, measured outcomes, deployment tradeoffs and remaining gaps
in the example README. Mark unsupported and unrun cases explicitly; a local lab
demonstration alone does not qualify a production deployment.

### Progressive testing across systems

Evaluate the repository's systems in **two or three passes**, returning to each
system to deepen coverage rather than exhaustively expanding one while the others
remain unexplored. State the systems in the current pass and respect the user's
priority order and scope.

1. **Survey every system in scope.** Read its upstream documentation and relevant
   source, intended strengths/limitations, deployment shapes, configuration and
   competing systems. Inspect existing examples and actual result files. Record
   what ran, known failures, missing evidence and the next useful tests. This is
   high-level analysis, not an execution or production-readiness claim.
2. **Establish runnable coverage across that scope.** Run each example's lifecycle,
   declared-operation correctness and representative workload; add the live
   writer/reader, durable witness, resource measurements and initial failure/
   recovery tests where missing. Verify routing/replicas where applicable. Record
   baseline results and gaps before moving on to the next system.
3. **Return for deeper production testing.** Revisit systems for applicable scaling,
   native repair, disk/dependency/quorum failures, partitions, upgrades, independent
   restore, pressure/soak and repeated comparative benchmarks. Vary workload and
   insert/query/cache approaches, guided by earlier results and system-specific
   failure boundaries. Continue subsequent rounds as new gaps or bugs emerge.

Prioritize sustained **performance comparisons for stable evaluated configurations**:
require the relevant correctness and recovery gates to pass first. For systems
with unresolved failures, investigate correctness/recovery before spending time
ranking throughput. Diagnostic timing/resource measurements remain useful, but
must be labelled as diagnostics and cannot establish a qualified performance winner.

For a smaller scope, combine the second and third passes into one follow-up pass;
keep the remaining coverage explicit. After each pass, update the per-example
README and TODO/audit with executed evidence, limitations and the next tests so a
future agent can resume. Prioritize genuine correctness/durability failures and
unvisited systems over repeatedly polishing an already well-covered example.
Finish and preserve an active experiment before switching its lab to another
system; avoid overlapping heavyweight benchmarks that spoil comparisons.

Run independent functional and recovery experiments in parallel when their
clusters, contexts, storage, networks, ports, journals and controllers are isolated.
Budget their **combined** CPU, memory and disk headroom, including workload actors,
witnesses, builds and dependencies; verify effective limits and runtime health.
Queue work that would exceed the host/VM budget. An unused role may be stopped
only when it is outside that test's declared topology; never reduce required
replication or durability to fit another experiment. Keep faults and lifecycle
operations serial within each owned example and preserve separate result histories.

Isolate comparative benchmark trials from other resource-intensive jobs on the
same host. Record actual co-tenancy in every run. Timings collected during
parallel functional tests are diagnostics; they do not establish an uncontended
performance winner. Compare intentional contention only as an explicit workload
with matching budgets and competing load on both systems.

These passes schedule the work; the evaluation stages and evidence requirements
below still gate qualification. An early pass does not waive missing production
tests, establish an unmeasured performance winner or turn a failed result into a
pass. Keep implementation/review passes separate from these testing rounds.

### Evaluation stages and backend contract

Use these stages to decide what evidence is needed, not to build a repository-wide
harness. Reuse the same logical workload, correctness oracle and metric definitions
within a comparison; retain each example's self-contained implementation and five
lifecycle commands. A failed prerequisite blocks qualification downstream; diagnostic
runs may investigate it but must not be presented as passing benchmarks.

| Stage | Question and required gate |
|---|---|
| 0. Declare the contract | Check in supported operations, payload/entity limits, consistency and atomicity boundaries, delete/TTL semantics, idempotency/conflict behavior, replication and durability settings, and safe retry rules. Verify effective settings, not just manifests. |
| 1. Driver and single-node correctness | Test every declared operation, exact round-trip values, missing/duplicate inputs, concurrent readers/writers and idempotent setup. Use isolated containerized fixtures; keep fast checks suitable for each code change. |
| 2. Healthy baseline | After functional correctness, measure individual operations and complete lifecycles on a healthy instance or small cluster. Sweep payload size, operation mix, concurrency and batch size; treat this as a baseline, not distributed qualification. |
| 3. Distributed correctness | Verify the declared consistency/transaction contract using concurrent operation histories, an independent oracle and all-member topology observations. A genuine contract violation blocks performance qualification regardless of speed. |
| 4. Distributed performance | After correctness passes, run sustained, repeated comparisons across relevant topology, replication, consistency, durability, schema/storage and client configurations at matched budgets. |
| 5. Chaos and recovery | Declare expected behavior per phase; prove each fault, observe live workload behavior, restore faults and rerun distributed correctness against the preserved history. |
| 6. Operational lifecycle | Exercise supported upgrades, scaling, repair, configuration changes, backup/restore and teardown under load, using the deployment-specific matrices below. |

Pin and report the reference client configuration: driver version, pool size,
maximum in-flight requests, connect/operation deadlines, retries, backoff and
idempotency-key reuse. Treat variations as explicit benchmark axes. Do not weaken
the declared contract after observing failures or compare weaker durability as if
it provided the same guarantees.

Settings readback does not prove effective protocol behavior. Inspect and exercise
timeout, retry and cancellation overrides; separately time connection setup,
execution/commit, fetching and error-path close. When claiming a deadline, test an
isolated stalled response and measure the actual bound, using an external worker
supervisor when native cancellation is insufficient. A deadline-terminated
mutation remains ambiguous until its effects are reconciled; do not classify it
as a known failed write merely because the caller stopped waiting.

Verify each system's actual model. Snapshot isolation can permit write skew;
serializable transactions must prevent it. Quorum overlap alone is not proof of
linearizability. Eventual convergence and permitted stale reads must be measured
against declared bounds, rather than universally rejected or silently ignored.

### Workload and correctness test types

Select applicable cases and explain omissions. Uniform tiny records and independent
single-key operations alone do not represent a production workload.

| Test type | What to exercise and verify |
|---|---|
| Operation and lifecycle coverage | Individual create/read/update/delete/list operations and chains such as create → mutate repeatedly → read all → delete. Verify all fields, derived counters/indexes, pagination and cross-operation agreement, not only row totals. |
| Parallel mutation and isolation | Same-key contention, different-key updates within one entity, concurrent creation/deletion, readers during writes and independent tenants. Verify lost updates, partial visibility and isolation anomalies against the declared contract. |
| Atomic operations and ambiguous outcomes | Cancel requests or lose responses during a declared atomic mutation. Reconcile whether the complete mutation committed; an error does not prove absence of effects. Partial visibility of a committed atomic operation fails. |
| Idempotency and retries | Replay the same logical operation with the same key after timeouts/reconnects, including concurrent replays. Test key reuse with different content against the documented conflict policy; distinguish dedup rejection, retry execution and duplicate side effects. |
| Payload and entity growth | Small/medium/large values, boundary sizes and rejected oversized inputs; cumulative state growth per entity as well as per-request limits. Include binary/string-heavy data, skew and varying compressibility. Report results per size class. |
| Access locality and load shape | Uniform baseline, hot-key/Zipfian skew, recent-key reads and sliding working sets. Include read-only/write-only and mixed ratios, bursts and sustained traffic. Separate successful existing-key reads from deliberate missing-key tests. |
| Saturation and backpressure | Sweep concurrency (for example 1/4/16/64 within budget), single/batch/bulk requests and offered load. Compare closed-loop workers with open-loop arrivals; record offered, admitted and completed rates, queue depth, deadlines and dropped work so client throttling cannot hide overload. |
| Delete, TTL and reclamation | Mixed expiry times, overwrite/expiry races, deletion during migration, restart and restore. Verify logical deletion/expiry separately from physical space reclamation, tombstone/metadata growth and compaction. Observe clock-dependent behavior without changing the shared host clock. |
| Application invariants | Check the advertised workload: queue loss/duplicates/order/leases and redelivery; transaction balances; search recall/version visibility; metric timestamp/value fidelity; or full object bodies and versions. Storage readback alone does not prove application correctness. |

Use deterministic seeds and record the actual size/key distributions. Where useful,
fit a sanitized distribution to observed percentiles; otherwise label synthetic
assumptions. Include rare large values and per-entity caps. Seed and verify baseline
state separately from timed steady-state work. Keep periodic exact write/read
verification active during benchmarks in addition to the independent readers.

Capture replayable operation histories with invocation/completion timestamps,
logical operation and attempt IDs, key/version, expected value or checksum, worker
sequence, payload bytes/class, result and error. Separate successful acknowledgements,
known failures, ambiguous outcomes and contract violations. Use an appropriate
offline history checker for linearizability/transaction claims; counters or a final
snapshot alone cannot establish them. Preserve witnesses and histories across faults.

### Additional resilience boundaries

These cases extend the deployment matrices; do not substitute a passing pod restart
for a different failure mechanism. Define expected rejection, allowed staleness/loss,
availability/error budgets and recovery deadlines before injection.

| Boundary | Additional tests and required observations |
|---|---|
| Repeated transitions | Repeat primary/replica role swaps, replica-count changes and scale-out/in chains. Verify every transition and cumulative history; one successful cycle can miss stale membership or leaked state. |
| Interrupted reconciliation | Reverse a scale-out while migration is in flight; crash/restart the controller during bootstrap, rebalance or drain. Verify no member owning data is removed, no ranges are silently recreated empty and the latest valid desired state converges. |
| Stale or empty member rejoin | Delay a replica, then fail its primary; separately restart an empty former primary at its old address. Verify promotion eligibility, new storage/member identity and fencing so an empty node cannot overwrite surviving good copies. |
| Compounded failures | After individual cases, fail another member during catch-up, lose a whole shard or all primaries, or lose the only up-to-date copy. Compare a supported healthy-quorum topology with a supported reduced-redundancy/beyond-tolerance case; quantify irreversible loss and backup requirements. |
| Network fault dimensions | Separately inject peer versus client latency, packet loss, bandwidth caps, one-way/asymmetric partitions, resets and DNS failure. Measure effective reachability and actual delay/loss; verify fault-tool admission/sidecars on running pods where used. |
| Pause versus crash versus loss | Compare a frozen process/owned worker, a crash loop, graceful eviction, sustained process outage, retained-node restart and permanent owned-node/storage loss. Hold faults beyond native detection deadlines where needed to exercise election rather than a quick restart. |
| Routing and connection recovery | Keep existing pooled connections and fresh clients active through failover, pod IP changes and Service/endpoint updates. Where advertised, test clients outside the cluster, returned/redirected addresses, DNS TTL/staleness, read-only old primaries and topology refresh. |
| Placement and blast radius | Check physical worker/failure-domain placement, all-member role/range agreement and restored replica count. Observe unaffected shards and, where applicable, several example-owned clusters during a shared controller/configuration change; detect coordinated rollouts and correlated loss of quorum. |
| Storage and maintenance pressure | Separate slow I/O, rejected writes, bounded disk-full, empty-disk replacement and background compaction/checkpoint/backup contention. Track write/read amplification, space growth, repair traffic and foreground latency until work settles. |
| Storage primitive contract | Where ownership or publication relies on file locks, atomic rename or exclusive creation, verify the actual primitive across independent processes and containers/Pods sharing that storage. Record mount/filesystem and reported inode/device identity; a matching pathname or successful syscall alone is insufficient. Use a small standard-library control to distinguish storage behavior from database behavior. A failed prerequisite blocks that storage configuration's ownership/durability qualification; preserve it when testing a different storage backend. A flush call alone does not establish physical power-loss durability. |
| Security/configuration lifecycle | When configured, rotate credentials/certificates under load, check revocation and allowed/denied access, and test invalid/live configuration changes. Verify effective data-plane settings after controller restart and declarative reapply. |

AP systems need not reject every minority write; assert their declared conflict and
convergence rules. A quorum-loss test may correctly reject operations, but a generic
95% success threshold must not turn that expected rejection into a failure or hide
violations in other phases. Declare per-operation success fractions, throughput and
latency targets separately. Record zero-attempt phases as missing coverage.

If testing client-side injected errors, report their rate separately from native
server/network errors and first-attempt failures; successful retries must not mask
them. After healing, verify all earlier acknowledged state, application invariants,
native repair and convergence. A manual rescue does not pass an automatic-recovery
requirement. Report integrity, availability, redundancy and manual intervention
separately; a healthy final cluster can still have lost data or missed its SLA.

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
  Collect the [mandatory resource/storage measurements](#mandatory-measurements-for-every-experiment)
  on the same phase timeline.
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
- For graceful shutdown, verify the effective image/Compose stop signal matches
  the actual PID 1. Replacing an image's entry point can change the required signal.
  Require a clean exit without timeout escalation; save stopped/recovered process
  inspection before assertions so a failed shutdown keeps its diagnostic evidence.
- Wait for native catch-up, log replay, repair, rebalancing and restored redundancy,
  as applicable. A Ready pod, successful survivor read or desired replica count is
  not enough. Verify the repaired member serves data; where supported, fail a
  different member afterward to prove the recovered copy is usable.
- Recheck effective native settings after each restart, failover and recovery,
  including durability, cache/ARC limits, worker counts and filesystem/module
  tunables. Container resource limits alone do not prove these settings survived.
  Persist required settings through the supported startup mechanism and verify
  actual values after boot and settling. Unexpected drift fails configuration
  qualification even when all acknowledged data survived; preserve those distinct
  outcomes, fix persistence and rerun. Do not repair settings mid-measurement to
  conceal a mismatched benchmark or recovery profile.
- Assert both availability and expected rejection: minority/quorum loss, stale
  primaries and read-only replicas must obey the documented consistency contract.
  Never lower replication, disable fsync or weaken consistency to obtain a pass.
  Quantify any data loss allowed by asynchronous replication or snapshot recovery;
  loss beyond the stated durability contract fails the test.
- Use bounded waits, propagate client/background-job failures, and save diagnostics
  on timeout. Restore injected faults on failure without deleting the evidence.
  Verify project/cluster labels and exact owned devices before destructive actions;
  never fill the shared Docker disk, reboot the host or touch another example.
  Honor stop requests during load, repair and verification. Serialize destructive
  cases per example, terminate owned background actors and flush witnesses before
  reporting. Recycle fixtures only between explicitly independent, recorded cases;
  never erase a failed case's acknowledged state to make its recovery check pass.
- Save dated raw results under ignored `results/`. Report workload and acknowledged
  counts, loss/corruption counts, retries/errors, throughput and p50/p99 before,
  during and after the change. Measure first successful operation, longest outage,
  election, catch-up and full-repair times separately where observable. State polling
  resolution, deliberate hold time, resources, versions and failure boundaries.
  One-host Compose/kind replicas do not establish independent-host durability.
- Verify teardown with healthy runtime/API listings that prove exact owned
  containers, networks, volumes, clusters and contexts are gone as applicable.
  A zero command exit status can accompany warnings that a resource remains in
  use. Include profile-only and one-shot resources in these checks. If exporting
  failed state, fence its owner first and preserve its storage when export fails;
  do not delete the only remaining copy or mark incomplete cleanup fully passed.

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

### Mandatory measurements for every experiment

**Every experiment, including correctness, chaos, recovery and single-system runs,
must measure CPU utilization, memory usage, data compression and disk usage.**
Benchmarks are holistic: operation rate alone is insufficient. Collect per-role
and whole-stack costs, including workload generators, replicas, controllers,
routers, WAL/metadata and object storage. Separate instrumentation/witness costs
from the system under test; do not sum the same shared filesystem once per pod.

| Measurement | Required evidence |
|---|---|
| CPU | Utilization time series and average/peak, normalization (one core = 100% or fraction of assigned CPUs), user+system core-seconds across threads/processes, effective CPU caps and throttling where available. Include setup/import, steady state and recovery/maintenance costs. |
| Memory | Average, sampled maximum and native peak RSS/cgroup memory where exposed; distinguish these counters and heap, anonymous memory, charged file cache and kernel memory. Show growth through sustained load and recovery; report OOM events and effective limits. |
| Compression | Codec/level and measured uncompressed/compressed bytes for the same logical data or snapshot at an identified layer, with ratio `uncompressed / compressed`. Distinguish storage, wire and backup compression. Include compression CPU/time cost and dataset compressibility; a configured codec is not a measured ratio. |
| Disk and storage | Owned bytes before load, after load, peak during work/repair and settled after cleanup/compaction. Distinguish logical versus allocated bytes, live data, indexes, WAL/AOF, snapshots/backups and temporary files; include all copies/object storage. Report bytes per logical record and whole-stack space amplification separately from compression. |
| I/O and network | Read/write bytes, IOPS/latency where exposed and network RX/TX, including replication/repair. Relate throughput to bandwidth, write amplification, queue/backlog growth and resource saturation; host filesystem capacity alone is not database disk usage. |

Start collectors before setup/load and retain phase-aligned observations through
baseline, fault, degraded service, repair and recovered state. Record counters,
units, scope, sampling cadence and collector overhead. For short operations use
cumulative CPU/I/O deltas and native high-water counters where possible; sampled
maxima can miss peaks. Report phase summaries and raw samples under ignored
`results/`, and put numeric resource/storage results beside latency/throughput in
each README. Efficiency measures such as CPU-seconds per successful operation,
bytes per record and completed operations per assigned core help explain tradeoffs.

One-shot tools must persist native counters before automatic container removal;
retain exit status and use runtime events for whole-container lifetime. Measure
the actual engine process (for example, Spark's JVM as well as its Python driver).
Respect counter resolution: very short intervals between coarse process-CPU ticks
can fabricate utilization spikes. Retain raw samples and CPU totals, state the
minimum interpretable interval and count suppressed utilization estimates instead
of publishing those spikes as peaks. Keep original and corrected reports separate.

If a metric cannot be collected, state **unavailable**, why, and the measurement
gap; never use zero or infer it from a resource limit. Use **not applicable** only
with a concrete reason (for example, no persisted data or no compression layer).
A correctness test may pass while its holistic benchmark evidence is incomplete.
Historical results lacking these measurements remain incomplete; rerun to fill the
gap rather than inventing estimates or retroactively claiming coverage.

### Performance and other production properties

Before each new performance-testing effort, **ask the user to choose a depth
from 1 to 4**. Recommend **level 3**, their usual preference, but wait for their
choice before starting performance runs. Record the selected level, scope and
time budget; ask again before expanding that scope. Do not automatically escalate
to exhaustive testing because a backlog or matrix exists.

| Level | Performance scope |
|---|---|
| 1 | Smoke measurement of one representative path; diagnostic numbers only. |
| 2 | Small baseline covering the main read/write paths and resource costs. |
| 3 | Focused practical evaluation: representative workloads, a small set of important variants and bounded tail-latency observations. Avoid exhaustive matrices and long soaks. |
| 4 | Broader repeated comparisons, saturation and selected degraded/recovery performance. |

**Never run level 5 testing.** The user revoked the previous level-5 approval.
Keep work bounded; their usual preference is level 3.

All levels retain correctness, failure honesty and the required resource/storage
measurements. Depth controls breadth, duration and repetitions, not evidence
quality. Report limitations when the chosen budget cannot support a capacity or
comparative claim; do not silently extend testing to meet that claim. Existing
correctness/recovery work can continue within its authorized scope while the
performance-depth question is pending.

- Measure **disk read bytes, disk write bytes and disk I/O** in every performance
  benchmark, separately from disk space usage. Report cumulative bytes and read/write
  rates (bytes/s or MiB/s), read/write IOPS and I/O latency/queue depth where exposed,
  per role and for the whole stack during each phase. State counter scope and
  sampling interval; distinguish logical engine I/O, cache hits and physical/device
  I/O. Record unavailable counters explicitly rather than treating them as zero.
- Run correctness and recovery checks before performance tests. Measure baseline,
  degraded operation, repair/rebalance and recovered state at stated offered load,
  concurrency, batch sizes, data sizes and resource caps. Include saturation and
  bounded soak tests for relevant systems; report queue growth, lag, memory/disk
  growth and failures, not only successful-request throughput.
- Separate API admission latency from durable commit, query visibility and
  end-to-end latency. State cold/warm caches, compression, retry policy, emulation,
  shared-host contention and run count; do not call a rate-limited fault workload
  or a tiny correctness fixture a capacity benchmark.
- Measure **time per operation**, not just operations per second. Report elapsed
  latency for each operation type in explicit **ms, µs or ns**, with mean and
  supported percentiles, sample counts and timing scope. Use a monotonic clock and
  state timer resolution/measurement overhead; nanosecond units do not imply
  nanosecond accuracy. For batches, report request/batch duration and batch size;
  any duration divided by record count is an amortized cost, not measured
  individual-record latency. Do not infer latency from reciprocal throughput under
  concurrency. Keep successful, failed and retried-operation timings distinct.
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
- Benchmark **data age, access temperature and operation type separately**. Reading
  a just-written value can hit a client cache, server cache or mutable in-memory
  state; it does not establish disk-read performance or strong consistency by
  itself. An older value can still be hot. Define the following applicable cases
  and report their latency, throughput, correctness and resource/I/O costs separately:

  | Case | Required distinction |
  |---|---|
  | Immediate read-after-write | Read the exact acknowledged key/version; state same session versus independent reader, primary versus replica, consistency setting, client-cache behavior and acknowledgement-to-visibility lag. Test the promised read-your-writes contract without equating it to linearizability. |
  | Older persisted-data reads | Read a separately seeded historical dataset after native flush/checkpoint/persistence where supported. State data age and verify persistence; elapsed time alone does not establish that data reached disk. |
  | Hot reads | Repeated reads of a small resident working set; distinguish result-cache hits from engine/page-cache hits and report working-set size. |
  | Warm reads | A partially resident or mixed working set with documented warmup, locality and measured cache-hit/miss counters where exposed. |
  | Cold reads | First access to controlled uncached data at explicitly identified cache layers; use a working set beyond cache capacity where practical and verify actual I/O. Do not call old data or a fresh client cold without evidence. |
  | Inserts versus updates | Separate new-key/append writes from overwrites/upserts, repeated updates of hot keys and updates to older resident/nonresident keys. Keep payload, indexes, durability and contention explicit; include read-before-write and partial/full update differences where supported. |
  | First-write versus sustained writes | Separate startup/allocation/cache-miss costs from warmed steady-state writes and later flush, checkpoint, compaction or eviction pressure. Define what a hot/warm/cold write means for this engine rather than assuming reads and writes share the same cache path. |

  Expose data-age ranges, working-set/cache sizes, access skew, read source and
  preconditioning as benchmark parameters. Keep query semantics and dataset shape
  comparable; do not mix these cases into one latency distribution. Include a mixed
  workload reading both recent writes and historical data while writes continue.
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
- Include the [mandatory measurements](#mandatory-measurements-for-every-experiment)
  in every run, on every competitor and deployment shape, not only comparisons.
  Begin each run with an environment/configuration record: actual CPU model/count,
  architecture, RAM/limits, disk class/capacity/limits, kernel and relevant filesystem
  tunables, Kubernetes/runtime versions, image digests, physical/simulated region/AZ
  placement, co-tenancy, source revision, seed and client configuration/hash. Record
  effective settings; redact credentials. No unlabeled cross-environment comparison.
- Use at least **three fresh-process trials** for comparative performance claims,
  alternate or randomize system order, and retain per-trial counts and samples.
  State warmup count and whether a summary is pooled or a median of trial summaries.
  Report percentile sample sizes; a p99 from a handful of requests is effectively
  a maximum, not established tail behavior. State benchmark rates' actual timed
  scope; reciprocal latency is not measured end-to-end throughput. Report winners
  per workload and resource cost rather than declaring a universal winner.
- For sustained performance comparisons, start with 60 seconds of warmup, at least
  5 minutes of healthy-baseline measurement or 15 minutes for a distributed matrix,
  and at least three independent trials per cell. Adjust with an explicit rationale;
  shorter diagnostics do not establish sustained capacity. Report median, min/max
  and IQR (or a justified confidence interval), per-operation and size-class counts,
  p50/p95/p99/p99.9 where sample counts support them, and histogram precision/bucket
  bounds. Separate setup, warmup and measured work; do not average percentiles across
  workers as if they were a pooled latency distribution.
- Retain every trial, including failed, interrupted and discarded runs. Unexpected
  environment changes invalidate an apples-to-apples cell; planned upgrades/faults
  belong to labeled phases. Never discard an outage, OOM or correctness failure to
  improve the winner. Show earlier failing runs alongside later passes, not just
  the latest result. Check report arithmetic, units, phase durations and histogram
  bounds against raw data; bucket upper bounds are not exact observed percentiles.
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

### Result artifacts and Git hygiene

Commit reproducible source/configuration, concise README results and compact
machine-readable summaries. Keep the per-trial/phase metrics, units, correctness
and recovery verdicts, limitations and provenance needed to assess the result.
Avoid redundant copies and unfiltered Docker/Kubernetes inspections, environment
dumps or per-request output in committed summaries; retain selected relevant fields.

Raw datasets, operation/witness logs, resource samples, database files, snapshots,
core dumps, downloaded binaries and materialized build outputs belong in ignored
directories such as `results/` and `.build/`. Before committing, check actual large
files with `git check-ignore`, inspect `git ls-files` and review staged paths: an
ignore rule does not remove a file that is already tracked. Preserve failed raw
evidence in verified local archives when reclaiming space; commit its compact
verdict/provenance rather than the archive, and never relabel the failure.

## Technology research

### Repository-only space cleanup

When space is low, inspect and reclaim generated files inside this repository
first. Keep automatic cleanup scoped to this repository. Remove only confirmed
untracked, ignored, reproducible caches, temporary files and build outputs.
Check Git tracking and active use before removal. Report the paths and measured
space recovered. The example's documented lifecycle may remove its own Docker
resources. Unrelated Docker images/volumes, global caches, other repositories
and host temporary directories require separate authorization for cleanup.

Preserve curated reports, acknowledged-operation histories, failed-run evidence
and the only usable recovery copy. Saved evidence is immutable. Exact duplicate
exports may share hard links after byte verification, with a local receipt of
paths and hashes; future tools must create new files instead of editing evidence
in place. Archive or retire larger evidence only through a verified preservation
procedure. A large ignored file alone is insufficient reason to delete it.

Before adding or materially changing an example for a technology, and before describing what it is good or bad at, read the relevant official manpages and inspect the corresponding source code. Use the documentation and source for the version actually used by the example when possible. Follow the execution path relevant to the example (configuration, query or API behavior, storage, replication, scheduling, or failure handling) rather than relying only on a project overview or benchmark headline. Use this research to choose representative workloads and explain tradeoffs; distinguish behavior confirmed in source from behavior measured by this repository's example, and cite useful upstream references in the README.

### Stable release selection

Use the **latest stable upstream release** for new evaluations and when returning
to an existing system. Check official release notes and supported deployment/
client compatibility at the start of the work; do not reuse an old repository pin
without checking. Prefer stable releases over nightly, preview, beta or release
candidates. Pin the verified version, image digest, client and extension artifacts
for the run; do not change them midway through a trial.

Verify the **actual native engine version**, not only a wrapper, package or image
tag. Language bindings may bundle an older engine. Record native versions and
extension/plugin revisions and checksums, and use documentation/source matching
those builds. If the newest engine is unavailable through a binding, investigate
an official native library, CLI or supported integration; disclose any remaining
blocker rather than silently substituting an old engine or claiming latest-version
coverage. Operators/charts must form a supported stable combination with the engine.

Older versions belong in explicitly labeled historical baselines, regressions or
upgrade/compatibility tests. Preserve their data and original failed evidence.
Recheck known failures and limitations on the current stable release before
presenting them as current system limitations; do not assume an upgrade fixes them.
Compare versions with equivalent workloads, resources, durability and correctness
checks. Test file-format and rollback compatibility on separate owned copies, never
by opening the sole historical database with a newer writer.

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
- Avoid writing Java unless the integration requires it (for example, a Kafka
  Connect extension). Prefer the languages above for application code and suitable
  non-Java languages for test/inspection tools. Using a system's existing JVM-based
  command-line tools does not require writing custom Java.
- For **new tests and benchmarks**, the general preference is **Rust, then C++,
  then Go, then Python**, in that order. Apply this to benchmark workloads and
  test/controller tools; Python is allowed but is not the default. Management
  code may also use another suitable language when justified. Choose the language
  that fits the system and existing tooling; this covers
  infrastructure orchestration, fault injection, recovery coordination, test
  assertions, result collection and report generation. These tools need not use
  the application's language. Keep this boundary clear; a workload generator is
  application code even when invoked by `make test`.
  Preserve a suitable existing harness rather than rewriting it solely to change
  languages; explain meaningful departures from the preferred order in the README.
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
