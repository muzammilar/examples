# S3/catalog partitions and ambiguous archive commits

The 2026-11-23 retained-source run passes **238 host controls** and the two endpoint
failure cases. It verifies **3,380,000 acknowledged hot rows**, **2,000,000 unchanged
cold-source rows**, and **3,000 write-probe rows**, with zero missing, wrong or
duplicate bodies. **2,000 probe rows are unacknowledged applied writes**, not ACKs.
The previous quiescence-deadline failure remains failed.
[Measured results and evidence hashes](DUCKDB_DEPENDENCY_RESULTS.json).

Use the example's five-command infrastructure lifecycle first. On a clean fixture:

```sh
make duckdb-clickhouse-dependencies ROWS=2000000
```

Make builds the local native network-fault helper automatically. Fresh cold-source
creation requires two free SeaweedFS volume slots and the existing 8 GiB host/Docker
headroom guards. This lab's preserved fixtures left one free slot. The measured
run therefore used an existing verified two-million-row table **strictly read-only**:

```sh
COLD_SOURCE_CASE=8a578883-bbdd-4429-b2bf-e4743c3c10b7 make duckdb-clickhouse-dependencies ROWS=2000000
```

That variable names an ignored local evidence case, not a prerequisite for clean
checkouts. Reuse accepts only a same-size, pre-fault fixture with a verified cold
body result and matching native table UUID. It saves source artifact hashes,
rechecks all cold bodies and requires the source snapshot to remain unchanged.
Faulted fixtures cannot bypass the fresh-copy capacity gate. New writes go to a
separate UUID-owned probe table; no failed state is reset or promoted to passed.
The final fresh-source variant was not rerun after the retained-source refinement.

Two independent controller tasks run ordinary native C++ DuckDB **2.0.1** SQL
through signed `clickhouse_scanner` **ce337c48c1** into ClickHouse **26.10.10.3**.
The writer seeds two million rows, then sends unique 10,000-row batches; the reader
checks 1,000 complete older/recently acknowledged bodies per request. Each task
waits 200 ms after a request (closed loop). The four-field fixture has Int64 ID,
Int32 customer, Int64 cents and a 64-character SHA256 payload: **168,000,000 logical
seed bytes**, excluding wire/storage overhead. Generation is deterministic, with
10,000 customers, no nulls and no RNG. The existing Python Docker/evidence controller
is retained; database application execution is native DuckDB, not a Python binding.

The helper uses pinned Debian plus native iptables **1.8.9/nf_tables**, **NET_ADMIN
only**, a read-only filesystem, 0.25 CPU and 64 MiB RAM. It joins only the verified
ClickHouse container's network namespace. A UUID-owned OUTPUT rule rejects TCP
traffic to the verified local SeaweedFS address, first port **8333**, then **8181**.
No host firewall, unrelated namespace or default Kubernetes context is touched.
These are **ClickHouse egress partitions**, not whole-service process outages.
Both backend processes remain running. The controller can inspect the catalog
through the storage container's unaffected local interface.

| Partition | Hot writes / reads OK | Cold read | Archive request | After healing |
|---|---:|---|---|---|
| S3, ~21.09 s controller phase | 47 / 47 | Client deadline | Client deadline; outcome ambiguous | All 1,000 intended rows applied; no retry |
| Catalog, ~21.09 s controller phase | 49 / 49 | Client deadline | Client deadline; outcome ambiguous | All 1,000 intended rows applied; no retry |

The network counters recorded **22 rejected packets / 2,847 bytes** for S3 and
**16 / 1,412** for the catalog. Each phase requires continued hot read/write progress.
Exact original namespace rules are restored after each fault. The helper is
stopped, inspected with PID 0, removed, and independently proved absent by a healthy
runtime listing. Raw rules, counters, process identities and phase markers remain
in the ignored case directory. The earlier rejected helper is retained stopped.

The important result is **timeout does not imply rollback or cancellation**.
An earlier diagnostic timed out after 12 seconds, then failed its 20-second server
quiescence deadline. After healing, its full 1,000-row body was observed committed.
That controller had only bounded Docker's client process, so the native DuckDB
process could also outlive the observation deadline. Its original failed verdict
and later read-only outcome observation are both retained.

The final controller additionally uses a **10-second native SIGKILL watchdog** for
cold/archive requests and proves each native client PID absent through a healthy
`/proc` listing. Even then, both server-side archive operations completed after
healing: closing/killing the native client did not undo the remote work. The live
hot writer/reader processes continue throughout; this watchdog does not pause them.
Every attempted request and returned outcome is fsynced outside ClickHouse.

Recovery waits for no owned archive query to remain active, loads the authoritative
catalog pointer, and verifies every expected cold value and key. A changed pointer
must expose the complete intended batch; it is not blindly replayed. Only a stable
pointer plus complete logical absence and fenced client/server quiescence permit
serial replay. The observed run took the **applied/no-retry** branch twice; the
absence/replay branch was not exercised by this final fault run. A separate healthy
1,000-row archive request then acknowledged, and full reconciliation verifies all
3,000 probe records. This is not a transactional or exactly-once guarantee for
arbitrary concurrent archivers.

Cold data queries explicitly use native SQL pass-through via DuckDB's connector.
Ordinary Iceberg catalog discovery and the earlier large-query wrong-answer issue
remain unresolved; this test does not qualify those paths. Query settings disable
Iceberg metadata/file caching and filesystem caching for the scoped cold checks,
but **OS caches were not cleared**. ClickHouse's
[data-lake guidance](https://github.com/ClickHouse/clickhouse-docs/blob/main/docs/use-cases/data_lake/guides/best-practices.md)
explains these distinct cache layers. Effective server S3/HTTP defaults were saved
read-only after the run; exact per-request settings are preserved in SQL files.

Hot request p50 latency during S3 loss was **236.692 ms write / 236.231 ms read**;
during catalog loss **229.626 / 222.594 ms**. The two ambiguous archive requests took
**10,068.602 / 10,038.841 ms**, including their explicit deadlines. The fresh healthy
archive took **312.866 ms**. Counts and p50/p99 are saved per role, outcome and phase;
p99 here is effectively a maximum, not a qualified tail distribution. These times
include Docker exec, process/connector startup, connection, SQL and transfer.
No capacity benchmark, reciprocal-latency throughput or comparative winner is claimed.

CPU over sampled role windows: ClickHouse **19.901 core-seconds / 28.65% of one core**,
DuckDB runner **58.533 / 87.38%**, SeaweedFS **1.496 / 2.15%**, helper **0.487 / 0.92%**.
The windows are 69.47, 66.99, 69.46 and 52.70 seconds respectively, including setup,
verification, probes and held-container time. Runner/helper cgroup peaks were
**126.76 / 5.36 MiB**. Server lifetime peaks can predate the case. Budgets remain
2 CPU/2 GiB runner combined, 2 CPU/1.5 GiB ClickHouse and 1 CPU/1 GiB SeaweedFS,
plus the helper. Host controller/Docker-daemon CPU is outside these cgroup totals.

Hot MergeTree parts contain **310,960,000 uncompressed / 231,785,862 compressed
bytes**, **231,916,911 bytes on disk**. An independent **footer-only** DuckDB/httpfs
audit measures the physical S3 table directories after recovery:

| Physical prefix | Files / rows | Encoded uncompressed bytes | Compressed chunk bytes | File bytes |
|---|---:|---:|---:|---:|
| Retained source | 2 / 2,000,000 | 152,417,677 | 72,952,027 | 81,427,999 |
| New probe | 3 / 3,000 | 280,481 | 133,908 | 163,662 |

Encoded chunk ratios are **2.089× / 2.095×**, not logical-input/file-size ratios.
The audit inspects physical prefixes, including potential orphan files; it does
not substitute for snapshot/body reconciliation or establish DuckDB Iceberg
interoperability. Here the observed file/row counts match the logical results.
Application requests still route through ClickHouse. Native allocated-disk and
per-device I/O bytes, operations and rates are retained: ClickHouse observed
**244,580,352 read / 565,424,128 write bytes**, **4,594 / 15,030 operations** on
Docker device 254:0. SeaweedFS observed 331,776 write bytes / 35 writes and no device
reads during its sampled interval, consistent with warmed file cache. These are
Docker-VM counters, not complete physical SSD costs. Runner/helper device counters
were unavailable; shared network-namespace counters must not be summed.

Pre-fault harness failures (Compose tmpfs quoting, Docker capability spelling and
TCP-reset protocol matching) remain failed. Rule installation/removal was separately
verified before the live partitions. Earlier-history/teardown preservation guards
remain in force. This test covers endpoint loss, ambiguous outcomes and recovery;
storage process crashes, independent-host durability, long soak and concurrent
compaction remain separate. Per `agent-query-safety` and `insert-batch-size`, the
queries are bounded and 10k hot batches are used; the isolated 1k archive probes
are correctness controls, not a bulk-ingestion recommendation.
