# DuckDB writers and readers through a ClickHouse process crash

`make duckdb-clickhouse-crash ROWS=2000000` passed its retained-volume SIGKILL
checks across 2026-11-22–23. The final run verifies **2,140,000 acknowledged rows**,
**zero missing, incorrect or duplicate acknowledged rows**, and **zero observed
applied rows from failed/ambiguous requests**. All **235** host controls pass.
Use the five-command infrastructure lifecycle in the example README first.
This is group 3 of the connector investigation, not complete architecture qualification.

The application is the native C++ DuckDB **2.0.1** CLI with signed
`clickhouse_scanner` **ce337c48c1**, connecting to local ClickHouse **26.10.10.3**.
Two independent controller tasks keep writes and reads running. Each request
starts a fresh DuckDB process inside this example's Compose runner; this tests
reconnect-on-request, **not persistent-session or connection-pool recovery**.
The Python controller drives Docker, journals intents/outcomes and checks returned
values; it does not replace the application connector with a Python database client.

The fixture uses the round-trip experiment's four fields: Int64 event ID, Int32
customer, Int64 cents and 64-character SHA256 payload. Seed IDs are `0..1999999`,
with deterministic `customer=id%10000`, `amount=id%10000+1` and
`payload=SHA256(decimal ID)`. No nulls or RNG. Seed logical field bytes are
**184,000,000**, excluding wire framing and engine overhead. Writers append fresh
10,000-row batches without retrying failed requests. Independent readers alternate
1,000 older seed records and 1,000 records from the latest acknowledged batch,
checking every returned field and ID in order against the host-generated oracle.

After proving live progress, the controller kills the exact owned ClickHouse
container, positively verifies **exit 137, PID 0, no OOM**, holds it down for eight
seconds, and restarts it with its existing volume. Writer and reader tasks continue
throughout. The new native process identity is recorded; unrelated SeaweedFS does
not restart. Four successful requests from each actor are required in both recovery
and post-recovery phases. The runner is fenced before final native reconciliation,
which also requires no owned INSERT still active.

The host fsyncs an intent **before each request** and its result immediately after
return. Failed writes remain ambiguous even when later observed absent. A full
native scan groups every stored ID by its witnessed batch, checks every body,
unique ID, range and acknowledged count, and rejects data outside the witness.
There is no inferred rollback, reset, blind retry or quorum/durability weakening.

| Request phase at start | Writes: OK / ambiguous | Reads: OK / failed |
|---|---:|---:|
| Baseline | 6 / 0 | 6 / 0 |
| Outage | 0 / 20 | 0 / 20 |
| Recovery | 4 / 2 | 4 / 2 |
| After | 4 / 0 | 4 / 0 |

Phase labels describe request start, and timestamps retain the real injection
boundary. A preceding trial had a baseline-labelled failed write that straddled
the fault; it was not a healthy-cluster failure. Fourteen live batches acknowledge
**140,000 rows** in addition to the two-million-row seed. Fourteen successful reads
verify **14,000 returned complete bodies**; final native reconciliation covers all
2.14 million stored records. There are **22** unacknowledged 10,000-row write
intents, all absent at reconciliation.

From the restart request, first observed successful write/read completion took
**1.991 / 1.961 seconds**. Last pre-fault success to first post-restart success was
**10.825 / 10.800 seconds**, including the deliberate hold, request spacing and
connection/startup costs; these are observed completion gaps, not exact service
outage durations. No leader election or replica repair occurs in this single-node
case. The reader's post-recovery success p50 was **225.215 ms**, maximum **279.558 ms**;
writer **240.187 / 269.272 ms**. These each have only four samples. The compact JSON
retains separate success/error per-phase counts, mean and nearest-rank p50/p99;
with these counts p99 is effectively a maximum. Timings include Docker exec,
DuckDB startup/connector load, connection, SQL and result transfer; they are not
query-only latency or capacity measurements. Each task waits 200 ms between
completed requests (closed loop). Caches were not cleared.

[Measured resources and raw evidence hashes](DUCKDB_CRASH_RESULTS.json):
ClickHouse **5.952 core-seconds / 43.04% of one core** across sampled native running
intervals totalling 13.83 seconds (including seed, verification and probes); combined writer/reader runner **15.624 core-seconds /
70.70%**, 22.10 seconds. Runner cgroup peak **86.39 MiB**; ClickHouse lifetime peak
**1,121.90 MiB** (may predate this case). Runner budget is **2 CPU / 2 GiB combined**,
ClickHouse **2 CPU / 1.5 GiB**, background SeaweedFS **1 CPU / 1 GiB**. The shared
Docker host is not a dedicated benchmark machine.

Stored parts: **196,880,000 uncompressed / 146,840,546 compressed bytes**,
**146,927,649 bytes on disk**. Per-role allocated disk, charged memory, CPU and
per-device read/write bytes, operations and rates are retained. ClickHouse samples
contain one expected process-generation gap; counters are not bridged through the
restart. Reported I/O covers sampled running intervals, not complete physical SSD
cost or delayed writeback. Runner device I/O is unavailable. Storage is background
only; this scenario does not write S3 or test an S3 outage.

Two prior runs also passed at 2.14 and 2.17 million ACKed rows. The initial harness run failed
before fault injection because setup and query JSON needed framing; its result,
SQL and native data remain preserved. An intermediate cleanup edit introduced an undefined variable after ClickHouse
had been restored; that failed run is also retained. Static checks and the final
rerun pass after correcting the cleanup order: restore the target before reporting
an actor-stop failure. The final run suppresses only setup stdout,
retains native errors, and rejects any reader mismatch including one finishing at
the final phase boundary. The earlier ordinary large-query wrong-answer finding
remains open: these successful bounded reads do not resolve it.

**Earlier-history re-verification is incomplete.** `make verify-history` refused
seven stopped legacy diagnostic containers whose project labels lack a verified
working-directory label, before scanning prior data. The guard was not relaxed and
no such container was removed. The saved check records exact rejected identities
and stderr; it does not imply data loss. One separate evidence-capture attempt
selected an older Python and failed its interpreter check; the retained successful
capture of the ownership rejection uses the explicit current interpreter.

This is a process-crash test with retained local storage and OS caches, not
physical power loss, replicated failover, independent-host durability, long soak
or complete production readiness. S3/catalog loss, concurrent maintenance and
transport-level ambiguous ACK injection remain separate unrun cases.
