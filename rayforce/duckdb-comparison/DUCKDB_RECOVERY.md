# DuckDB native live WAL recovery

Use the five-command [Quick start](README.md#quick-start), then run these before
teardown:

```sh
make test-duckdb-connectivity
make test-duckdb-recovery
make report-duckdb-recovery DUCKDB_RECOVERY_RESULT=results/duckdb-live/<explicit-run-id>
```

The serial `make test-production-recovery` now runs these two DuckDB gates plus
the six existing Rayforce recovery cases. `make test-production` also retains the
Rayforce header, payload and AOF corruption diagnostics; their documented integrity
failures still produce a nonzero result. Passing process recovery does not qualify
those different fault models.

## Architecture and contract

One C++ process owns the persistent DuckDB **2.0.0** database. Three independent
C++ applications use the official **Quack** SQL session protocol: control, writer
and reader. Python schedules faults and drives the same deterministic logical
workload as Rayforce; it fsyncs intents and outcomes outside the failed container.
All source, CMake definitions, pinned extension hashes, Compose configuration and
controllers are local to this example. No host application compiler is required.

| Component | Effective limit and role |
|---|---|
| Native database | 1 CPU, 4 GiB, no swap; one engine thread, 3 GB engine memory limit |
| Each SQL application | 1 CPU, 512 MiB, no swap; 384 MB local engine memory limit |
| Whole running application stack | 4 CPU / 5.5 GiB ceilings; measured usage is separate |
| Storage | Unique labeled Docker volume, Linux filesystem; no host-shared database file |
| Network | Internal Compose network, no published ports; TLS and token authentication |
| Signed native extensions | Quack `f96420ed46`, HTTPFS `77392a7207`; [checksums](quack-sources.json) |
| Live write / read ceilings | 50 writes/s / 20 reads/s, paced closed-loop |
| Request policy | No mutation retries, HTTP retries or Quack request replay; writer/reader native deadline 3 s |
| Control policy | Native deadline 180 s for seeding/export/checkpoint; outer process deadline 190 s |

Clients use `ATTACH` followed by `CONNECT` to forward the complete SQL session.
The certificate fingerprint comes from the trusted native server log, then remains
pinned across restarts. Wrong-token and wrong-fingerprint connections must fail
natively while the original server and acknowledged probe stay intact. The image
contains signed extensions; execution does not download extensions or disable
signature verification. Only ARM64 execution has been qualified; AMD64 artifacts
are pinned but their runtime path is unrun.

Each successful autocommit `INSERT` acknowledges one row after the native durable
commit path. Every failed response remains a potentially applied intent until
reconciliation; the adapter reconnects for the **next operation**, never replays
the failed mutation. The reader alternates a recent acknowledged row and an older
seed row, verifying every field. These are resident-data point predicates, not
cold-cache or linearizability tests. Tables have no explicit index; the engines
may optimize/prune their native physical plans differently.

## Fault procedure

1. Verify/load **2,000,000 six-BIGINT trades and 10,000 customers**, checkpoint,
   inspect settings and native storage codecs, and establish independent progress.
2. Keep both applications running; record a nonempty WAL and kill the actual
   owner with SIGKILL. Require exit **137**; hold it down **5 s**.
3. Restart the same container/volume, verify a new generation and successful live
   reads/writes, and observe **15 s** of recovery.
4. Run native `FORCE CHECKPOINT` without pausing either application, accumulate
   another **10 s** of new tail traffic, then repeat the crash/recovery cycle.
5. Stop applications only after both recovery phases; export ordered complete
   rows, close native handles, require graceful SIGTERM exit **0**, and copy the
   stopped volume. Replay the saved witness against every seed/customer/ACK body.
6. Remove only owned containers, network and volume after the export is verified.
   Keep ignored raw histories, resource samples and native state. Failed runs remain
   failed; an export failure preserves the owned volume for diagnosis.

The admission marker rejects an unmarked existing database or a missing file with
a retained marker. It is not a backup or a detector for losing an entire volume.
TCP health is only port liveness; the controller verifies native SQL readiness.

## Measured results

The committed [result document](DUCKDB_RECOVERY_RESULTS.json) contains the exact
fixture/image/source identities, per-phase counts/rates/latencies, resources,
native file hashes, compression and witness digest. The table below is generated
from the selected final run, not the earlier bootstrap probe.
The [qualification receipt](DUCKDB_RECOVERY_QUALIFICATION.json) identifies the
final TLS controls, regression/lifecycle run and full suite's failed integrity cases.

<!-- measured-results -->

**2026-11-22, Apple M4 Pro, native ARM64 Docker Desktop VM.** Selected run
`c1e70f9006f24798bf9871cc60df57c4`; one functional observation, not a capacity
benchmark. Input CSV is **52,801,261 bytes** of trades plus **92,663 bytes** of
customers; fixture seed **20261117**. Native file exports contain every seed and
live body, not just matching totals.

| Check | Result |
|---|---:|
| SIGKILL / retained-volume recovery cycles | 2 |
| Seed trades / customers verified | 2,000,000 / 10,000 |
| Write intents / ACKs / error-or-ambiguous responses | 2,403 / 2,364 / 39 |
| Recovered live bodies / applied unacknowledged intents | 2,364 / 0 |
| Missing ACKs / corrupt or duplicate bodies | 0 / 0 |
| Independent successful / failed reads | 1,006 / 31 |
| Baseline successful writes / reads | 44.741 writes/s / 19.157 reads/s |
| Baseline write p50 / p99, 482 samples | 2.207 ms / 4.048 ms |
| Baseline read p50 / p99, 206 samples | 1.852 ms / 3.118 ms |
| Recent-ACK baseline read p50 / p99, 103 samples | 1.735 ms / 2.986 ms |
| Historical-resident baseline read p50 / p99, 103 samples | 1.961 ms / 3.292 ms |
| Cycle 1 first successful write / read after start request | 600.325 ms / 510.089 ms |
| Cycle 2 first successful write / read after start request | 417.971 ms / 609.169 ms |
| Cycle 1 observed write / read success gap | 5,781.551 ms / 5,710.614 ms |
| Cycle 2 observed write / read success gap | 5,611.899 ms / 5,790.147 ms |

Both outage phases produced errors in **both** applications. Baseline and
post-checkpoint-tail traffic had zero errors. Error request latencies and all
phase/temperature distributions remain in the result document. Baseline rate
windows were **10.773 s** for writes and **10.754 s** for reads, including external
controller/witness pacing; they are not reciprocal native request latencies.

| Role | Observed CPU core-s | Baseline mean CPU, one core = 100% | Native cgroup peak, MiB | Native process peak RSS, MiB |
|---|---:|---:|---:|---:|
| Database, three generations | 15.743* | 25.02% | 221.94 | 219.71 |
| Control SQL application, including seeding/export | 1.123 | 0.55% | 44.44 | 94.61 |
| Writer SQL application | 13.298 | 2.68% | 47.13 | 99.63 |
| Reader SQL application | 9.600 | 1.09% | 44.97 | 96.14 |

`*` Sum of the latest native observation per server generation; two killed
generations are lower bounds. Client totals include reconnect/error handling
through outages; baseline CPU alone would hide that cost. Server baseline peak
sampled CPU was **52.49%** of one core. Shared image pages can appear in process
RSS while being charged to a different cgroup; do not equate the two peaks.
Across **126** observations with all four roles alive, summed sampled charged
memory averaged **198.18 MiB**, maximum **285.75 MiB**; role samples are collected
serially, not atomically. There were **166** resource observations overall.

| Storage / I/O / controller metric | Measured value |
|---|---:|
| Native database before seeding | 12,288 logical / allocated bytes |
| Native data file after seeding/checkpoint | 14,954,496 logical / allocated bytes |
| Maximum native files during live sampling, including WAL | 15,477,680 logical bytes |
| Settled native file after graceful close, WAL checkpointed away | 15,478,784 logical / allocated bytes |
| Settled native file gzip level 1 | 12,075,231 bytes; 1.282× archive ratio |
| Archive compression wall / CPU cost | 170.747 ms / 170.720 ms |
| Observed server block read / write bytes, all generations | 0 / 77,987,840 bytes* |
| Observed server block read / write operations, all generations | 0 / 2,503 operations* |
| Final-generation lifetime block write rate / write IOPS | 3.017 MiB/s / 46.086 IOPS |
| SQL application block read / write counters | 0 / 0 bytes each |
| Host controller wall / self CPU | 71.846 s / 5.343 core-s |
| Host controller peak RSS | 49.39 MiB |
| Volume initializer observed CPU / cgroup peak | 0.060 core-s / 9.91 MiB |

The selected native codec query returned **BitPacking** for six BIGINT columns and
**Constant** validity segments. The 1.282× figure is an additional gzip archive
measurement, not DuckDB's native codec ratio. Native sampling excludes CSV exports;
the settled file can exceed the last live sample during final checkpoint/close.
The zero read counters describe this cached Linux cgroup observation only. The
final-generation I/O rate includes reconciliation/export and is not steady-state
write capacity. Killed-generation block totals marked `*` are lower bounds.

<!-- end-measured-results -->

Timings are native C++ request round trips, including connection/reconnection and
result conversion but excluding the later host witness fsync. Successful/error
distributions are separate. Recovery includes Docker start, WAL replay, client
pacing and deadlines; observed gaps include the deliberate hold. Small-sample p99
is descriptive nearest rank, not an established tail bound. No capacity winner is
claimed from these short functional observations.

Resource samples wait 200 ms **plus serial Docker inspection/exec overhead**.
CPU intervals crossing phases or server generations are excluded. Native
process RSS, cgroup charged memory/peak and sampled means are separate counters.
Post-close counters cover all three SQL applications and the final database
generation; pre-kill observations are lower bounds for the two killed generations.
Instrumentation exec costs are included. I/O bytes and operations are Linux
cgroup block accounting; rates use the stated native/container observation
windows. Physical host device latency, queue depth and host disk writes are
unavailable. A cached read's zero block counter does not mean zero physical cost.
Host controller CPU/RSS are measured separately; Docker CLI subprocess and
daemon/VM CPU are outside that self-process counter and remain unmeasured here.

Native file bytes exclude exported CSV evidence. Input/native size is **space
amplification**, including metadata and page slack, not a codec ratio. Native
`pragma_storage_info` identifies actual codecs; separate gzip level-1 measurements
describe only the saved file's archive compressibility. The raw state includes TLS
keys and lab credentials and remains ignored; it is not committed.

## Comparison boundaries and remaining tests

Both engines now use the same fixture, live operation IDs, full-body witness,
offered ceilings, server/application budgets and retained-Linux-volume fault shape.
Their native protocols and durability paths differ: Rayforce logs synchronous IPC
requests, including reads, while DuckDB's WAL records transactional changes.
Quack uses TLS here; the Rayforce lab uses plaintext internal IPC. Therefore
wire/log bytes and latency are not identical-protocol comparisons. A single
functional run per selected report does not rank recovery speed or resource cost.
Repeated alternating fresh trials and sustained load remain necessary.

Not qualified: independent-host durability, replication/failover, device/power
loss, disk-full/EIO, total-volume replacement, online backup availability, torn or
interior WAL corruption, interrupted checkpoint publication, network partitions,
larger-than-memory pressure, prepared SQL, and verified OS-cold queries. The final
stopped-volume export is retained evidence, not a tested independent restore.

## Inspected upstream behavior

- [DuckDB 2.0 concurrency](https://duckdb.org/docs/2.0/connect/concurrency) and
  [Quack session reference](https://duckdb.org/docs/2.0/quack/reference): one native
  owner with independent remote SQL sessions; this is not shared-file multiprocess writing.
- [Pinned commit path](https://github.com/duckdb/duckdb/blob/v2.0.0/src/transaction/duck_transaction_manager.cpp)
  and [WAL flush](https://github.com/duckdb/duckdb/blob/v2.0.0/src/storage/write_ahead_log.cpp):
  transactional WAL work and synchronous flush; this lab tests process crashes,
  not whether a physical device honors flush during power loss.
- [Checkpoint behavior](https://duckdb.org/docs/2.0/sql/statements/checkpoint):
  `FORCE CHECKPOINT` waits for the checkpoint lock, rather than aborting clients.
- [Pinned Quack transport](https://github.com/duckdb/duckdb-quack/blob/f96420ed46/src/quack_client.cpp)
  and [native server](https://github.com/duckdb/duckdb-quack/blob/f96420ed46/src/quack_start_stop.cpp):
  fingerprint-pinned TLS, explicit timeouts and disabled reconnect replay. Native
  TLS teardown has a separate bounded timeout and can extend failed-request time.
  Research used the [exact source archive](quack-sources.json), not only current docs.
