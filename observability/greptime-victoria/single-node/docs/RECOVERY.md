# Process loss and application witness recovery

Tested 2026-11-28: GreptimeDB 1.3.1, VictoriaMetrics 1.157.0 and VictoriaLogs
1.55.0, at the README's matched 4 CPU / 4 GiB backend budget. These are
single-process SIGKILL/retained-volume tests, with a ten-second held outage.
They do not model permanent disk loss or physical power failure.

The two example-owned writer/readers stay live through each fault. Each has
0.5 CPU / 256 MiB; their aggregate is the ordinary workload's 1 CPU / 512 MiB.
Every request has an fsynced intent and response witness outside backend storage.
HTTP errors/timeouts remain ambiguous; they are not automatically retried or
called known failed writes. Independent readers query old verified data and
sample recent acknowledgements. Their five-second anchor rotation is a diagnostic,
not a backend visibility contract. Record visibility delays, not fabricated losses.

`make live-control`, `make crash-metrics`, and `make crash-logs` are independent,
serial, observed targets. `make test-production` runs those three followed by
retained-history verification. Its component cases were executed separately;
the combined target has not been redundantly rerun. The log case kills Greptime's
unified process or VictoriaLogs; the metric case kills Greptime or VictoriaMetrics.
Both signals continue on the Victoria side, exposing its different blast radius.
Native exit 137, non-OOM status and the same container/storage identity prove the
fault and restart. Recovery requires a new post-restart acknowledgement visible
through the independent reader, not merely a healthy HTTP endpoint.

## Observations before any replay

| Case | Greptime metric/log ACKs retained | Victoria metric ACKs missing / total | Victoria log ACKs missing / total | Kill → fresh ACK visible, Greptime / affected Victoria role |
|---|---:|---:|---:|---:|
| Metrics crash, first | 19,000 / 18,000 | 47,000 / 95,000 | 0 / 141,000 | 13.178 s / 18.683 s |
| Metrics crash, repeat | 19,000 / 18,000 | 48,000 / 96,000 | 0 / 142,000 | 13.174 s / 18.763 s |
| Logs crash | 19,000 / 18,000 | 0 / 131,000 | 29,000 / 80,000 | 13.209 s / 16.000 s |

No acknowledged Greptime record was missing. Every missing Victoria acknowledgement
predated the restarted process. Native VictoriaMetrics JSON export independently
confirmed the first missing 47,000 IDs; the raw export remains ignored. Full
metric-label/value/timestamp and log-body/tenant/timestamp checks reconciled all
original acknowledged records after the explicit witness replays. No unexpected
IDs or duplicate log bodies appeared. Ambiguous requests remain separately
counted, including 49,000 absent metric records in the repeated Victoria crash;
absence at a frozen observation cut does not turn their earlier RPC errors into
known failures.

The first metrics case **remains failed** in the report: the initial controller
stopped on ACK loss. The later successful recovery does not rewrite that status
or establish native zero-loss behavior. A separate first replay-controller attempt
failed before mutation because its evidence directory was absent; that controller
error/log also remains preserved. The corrected replay ran once with a new journal.

## Why these persistence contracts differ

Greptime's checked-in raft-engine configuration enables synchronous WAL writes.
Victoria's HTTP admission can precede periodic persistence. Its default-buffered
profile therefore cannot be ranked as an equivalent synchronous-durability
configuration merely because both accept the same protobuf request.

The pinned VictoriaMetrics binary reports `inmemoryDataFlushInterval=5s` by
default. This is **not** a promise that every acknowledged request is durable
within five seconds. In the pinned [partition implementation](https://github.com/VictoriaMetrics/VictoriaMetrics/blob/v1.157.0/lib/storage/partition.go),
in-memory parts receive a flush deadline, a periodic worker selects expired
parts, and parts already merging are skipped. Buffering/deadline/scan timing can
extend the observed acknowledged tail. This is a source-informed explanation,
not a proof that each missing request followed one specific internal schedule.
See [VictoriaMetrics storage and flags](https://docs.victoriametrics.com/victoriametrics/)
and [VictoriaLogs storage](https://docs.victoriametrics.com/victorialogs/).
No upstream correctness bug is established by loss permitted by buffered admission.

## Recovery runbook and limits

1. Preserve the failed fixture, request journal, process restart evidence and
   complete `reconciliation-before.json`. Stop and fence both workload actors.
2. Recover the same owned process/storage and verify endpoint health. Run full
   original-history readback, including ambiguous outcomes and independent log
   bodies; never reseed to hide loss.
3. Replay only IDs still missing from the original acknowledged set, with unchanged
   original values and timestamps. The replay validates the fixture, signal,
   protocol, source-history digest and pre-restart acknowledgement cutoff before
   any mutation. It refuses new-process ACKs or unproven candidates.
4. Write replay requests into a new exclusive journal, then verify every original
   ACK again. Preserve both before and after reports. Automatic buffered-loss
   recovery is limited to the role actually killed; unexpected loss elsewhere fails.
5. Re-run `make verify-retained` before any destructive cleanup.

`make recover-witness CASE=<preserved-32-hex-run-id>` exposes the explicit recovery
for a metrics/log crash halted after its initial loss report. It refuses a live
actor and reusing a replay journal. If a replay itself fails or has an ambiguous
outcome, preserve that journal and reconcile it before designing another attempt;
blindly rerunning can duplicate append-only logs. A proven-dead old process,
frozen writers, exact missing-body checks and no automatic request retries are
prerequisites for the log replay used here. This is application-level repair,
not a claim that arbitrary retries are safe or that a general collector is
exactly-once. A production collector also needs its own persistence/retry policy.

The successful replay of these bounded cases depends on the external witness.
Without an independent retained source/journal/backup, acknowledged buffered loss
is unrecoverable by this demonstrated procedure. A local witness on the same
host does not survive that host's permanent loss. Runtime publication is not a
physical power-loss/fsync guarantee.

## Resource evidence

[RECOVERY.json](../reports/RECOVERY.json) retains each case, original failure,
source/configuration, request/read timings, wire compression, disk snapshots and
native cgroups. Observed invocation windows include retained-history preflight,
backend switching and verification; they are **not isolated per-engine costs**.
The repeated metrics invocation used 24.955 CPU core-s, sampled peak aggregate
memory 843,005,952 B, disk read 651,264 B and write 13,828,096 B. The logs invocation
used 25.159 CPU core-s, peak 834,801,664 B, disk read 462,848 B and write 6,832,128 B.
Network counters, per-role CPU utilization and mean memory are in the report.
Short replay-tool cgroups complement Docker samples that can miss sub-second work.
An empty/unreadable I/O counter is unavailable, not zero. Native codec compression,
physical device I/O latency/queue depth and unavailable Docker IOPS remain gaps;
wire gzip and allocated storage are reported with their distinct meanings.
