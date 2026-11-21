# Rayforce synchronous journal recovery

Use the five-command lifecycle in [README.md](README.md), with these additional
tests before teardown:

```sh
make test-journal-capability  # native IPC/status/scalar replay gate
make test-journal-recovery    # independent writer/reader through two SIGKILLs
make test-production          # run those implemented cases serially
```

`JOURNAL_BASELINE_S=10`, `JOURNAL_OUTAGE_S=5` and `JOURNAL_RECOVERY_S=15`
are overridable; the second snapshot gets another baseline interval. These are
bounded recovery tests, not sustained capacity benchmarks. `make down` and
`make docker-down` refuse teardown while journal containers remain. Normal test
cleanup removes its containers/network and preserves raw state and witnesses.
Do not remove failed state to make a rerun pass.

## Deployment and contract

The example-local [Compose file](docker-compose.journal.yml) deploys one native
Rayforce server and three independently running C++ native IPC applications:
control, writer and reader. CMake builds the adapters against the same pinned
source as the embedded comparison. Python drives the test and fsyncs the external
intent/ACK/read history on the host, outside the crashed server container.

The server uses **`-L /state/journal`**, **`RAYFORCE_NO_FSYNC=0`**, one engine thread,
**1 CPU / 4 GiB**, swap disabled. Each IPC process gets **1 CPU / 512 MiB**, also
without swap. The internal Compose network publishes no host ports. These caps
are inspected, not inferred from the file. Startup scripts never reset seed data
on recovery: the same native journal/snapshot and retained bind mount are reopened.
Authentication and TLS are not configured; this is an isolated lab network.

Each mutation has a unique, fsynced intent before sending. There are **no mutation
retries**. The native API has a 3 s connect deadline and 3 s round-trip deadline;
a send error can follow application, so every intent is reconciled. Readiness
probes may retry separately. The independent reader alternates exact complete-body
probes of an older seeded row and the latest externally recorded ACK. These are
resident full-scan point queries, **not OS-cold reads** or a linearizability proof.

The pinned source journals **all synchronous IPC requests**, including reads,
before evaluation. This test includes that cost. A server request returning an
error is not transactional rollback evidence. Loading tables also returns native
table objects over IPC to the control process; its resource/network costs are
included rather than hidden in server-only figures.

The controller saves native state, proves exit **137**, holds the server down,
starts the same container and verifies a new process generation. All three IPC
containers must retain their identities/start times. Writers and readers continue
through both outages and native recovery. A second snapshot and new journal tail
precede the second crash. Final ordered exports reconcile every old/new row from
the **saved external history**, plus every customer body from the input fixture.

## Executed results

**2026-11-21**, native ARM64 Docker Desktop VM on Apple M4 Pro; pinned development
commit `38fd4be3560d63a5514c1e6883685fe496d45e1a`. Same synthetic six-I64
**2,000,000-trade / 10,000-customer** fixture as [PERSISTENCE.md](PERSISTENCE.md).
Offered ceilings are **50 writes/s** and **20 reads/s**; the closed-loop actors
include host orchestration/fsync and do not establish saturation throughput.
Observed baseline completion rates were **43.61 successful writes/s** over
**10.342 s** and **18.89 successful reads/s** over **10.321 s**. Each window runs
from that actor's first invocation to last completion in the baseline phase.

| Check | Observed result |
|---|---:|
| Retained-state SIGKILL/replay cycles | 2 |
| Seed trade/customer bodies verified | 2,000,000 / 10,000 |
| Mutation intents / ACKs / errors | 2,867 / 2,251 / 616 |
| Live bodies recovered | 2,251 |
| Applied unacknowledged intents | 0 |
| Missing ACKs / wrong or duplicate bodies | 0 / 0 |
| Successful / failed independent reads | 971 / 266 |
| Baseline write p50 / p99, 451 samples | 0.341 ms / 13.796 ms |
| Baseline read p50 / p99, 195 samples | 12.638 ms / 16.387 ms |
| Cycle 1 first write / read after start request | 1,906.853 ms / 1,935.558 ms |
| Cycle 2 first write / read after start request | 1,922.458 ms / 1,952.126 ms |
| Cycle 1 observed write / read success gap | 7,132.789 ms / 7,171.458 ms |
| Cycle 2 observed write / read success gap | 7,093.480 ms / 7,144.402 ms |

RPC timings include native connection/request/formatting, excluding the subsequent
host witness fsync. Successful and failed latencies remain separate in the JSON.
Recovery observations include Docker startup plus replay, client pacing and the
deliberate 5 s hold; they do not isolate engine replay. Outage markers precede the
Docker kill call, so a few operations complete during that transition. Small-sample
p99 values are descriptive nearest-rank observations, not established tail bounds.

| Role | Observed CPU core-s across generations | Observed cgroup peak, MiB | Observed PID 1 peak RSS, MiB |
|---|---:|---:|---:|
| Server | 16.090 | 524.6 | 475.8 |
| Control IPC | 1.241 | 421.3 | 390.1 |
| Writer IPC | 0.398 | 13.7 | 14.8 |
| Reader IPC | 0.223 | 13.4 | 14.7 |

Server average CPU in same-generation sampled intervals was **23.0%** of one core
at baseline, **30.2%** in recovery 1 and **30.9%** in recovery 2. Native checkpoint
counters are observed lower bounds on lifetime CPU and peaks; instrumentation
execs are included. Sampling retained **229 observations**, nominally 200 ms plus
serial Docker API overhead. Phase/generation-crossing CPU intervals are excluded.
Process RSS, cgroup charged memory and sampled memory are different counters.

Settled snapshot/journal/lock files totaled **96,667,650 bytes**; sampled native
maximum was also **96,667,650 bytes**, which does not exclude shorter missed peaks.
Verification CSVs are excluded from that total. Off-path gzip level 1 of those
same files produced **22,991,378 bytes**, **4.205×**, costing **363.955 ms CPU** and
**366.190 ms wall time**. That is archive compressibility, **not native encoded
column compression**. This journal snapshot differs from the splayed-table format.

Native cgroup disk read/write byte/operation counters were exposed but empty;
this does **not** establish zero physical I/O. Docker Desktop bind-mount work can
occur outside the cgroup. Device latency/queue depth and physical host I/O remain
unavailable. Network counters are retained per role/generation; the host controller
used **9.738 CPU core-s**, **566.9 MiB peak RSS**, including verification and gzip.
Docker CLI subprocess CPU/memory, daemon and VM overhead are outside that host
process measurement; whole-host cost remains incomplete.

[JOURNAL_RESULTS.json](JOURNAL_RESULTS.json) is the explicit selected result with
source hashes, image identity, client/environment config, phase outcomes, native
counters and measurement gaps. The publisher rejects changed source or fixture,
missing kill proof, resource mismatches and incomplete cleanup. Raw evidence is
under `results/journal-live/3471333d96874fcf9ea8691d38964da9/`.
To republish that explicit run, use
`make report-journal JOURNAL_RESULT=results/journal-live/3471333d96874fcf9ea8691d38964da9`.
All six Python/C++/Rust × engine regressions, 18 native negative controls and
19 controller tests passed afterward. Earlier development runs remain retained:
one failed adapter omitted the required native poll, another expected quoted
symbol formatting; two earlier full live runs passed with narrower assertions.

## Remaining boundaries

This is one authoritative local process, not replication/HA or physical power-loss
testing. The equivalent DuckDB live application boundary remains unimplemented,
so these results are **not a recovery/performance comparison with DuckDB**.
Asynchronous journal/watermark recovery, interrupted snapshot publication,
torn-tail/interior corruption, second-writer exclusion, independent backup
restoration, isolated resource pressure and repeated sustained/cold performance
remain unrun. Recovery did not delete or truncate any journal.

Sources: [native journal API](https://rayforcedb.com/docs/namespaces/log/),
[instance ownership](https://rayforcedb.com/docs/guides/instances/),
[pinned IPC journal path](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/core/ipc.c),
[pinned recovery code](https://github.com/RayforceDB/rayforce/blob/38fd4be3560d63a5514c1e6883685fe496d45e1a/src/store/journal.c).
