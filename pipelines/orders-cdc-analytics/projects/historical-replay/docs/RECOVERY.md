# Interrupted historical publication

## Original failed run

Fixture `ceefdbf02e344388913b3731d7119936`, historical case
`4b96e520ab2b475f94addeceef059c0e`. Spark's 200,000 rows matched every exported
field. Both native transactions prepared and the publisher was SIGKILLed without
OOM. Source ACKs advanced 118 → 119 while it was down. The controller then failed
because it required immediate VISIBLE after commit, although Doris commits before
asynchronous replica-version publication completes.

Native transaction 254 committed at 02:51:08 UTC and became visible at 02:51:15:
a seven-second native timestamp interval. Recovery of transaction 259 observed
COMMITTED explicitly after its successful HTTP commit; native visibility followed
at 02:51:37 after a 02:51:36 commit. The failed command's stdout was initially
missing from the controller exception; retained recovery commands captured native
responses/state. The controller now includes failed native output in its error.

Repeated commit of a finished label returned ANALYSIS_ERROR (`running transaction
not found`), even though SHOW TRANSACTION confirmed the original label VISIBLE.
Treat neither that error as proof of failed mutation nor an HTTP success as proof
of query visibility. The fixed application accepts only native VISIBLE for the
same saved label, polling COMMITTED within a bounded visibility budget. Other
unexpected states fail; there is no automatic submission under a new label.

Both original labels were recovered in place, not replaced. All three fixed
replicas then matched 999,672 active source rows, and 166 source ACKs reconciled.
The original phase status remains failed. Its subsequent successful recovery is
a separate outcome, not a rewritten result.

## Corrected controller case

Case `6621948fd9e74e29bdb7470895afb5f1` reused the preserved fixture and exported
a new, explicitly different snapshot. 199,672 rows independently matched Spark's
output. The source writer advanced 10,000 rows beyond those snapshot versions,
including 572 cancellations. Both prepared batches survived publisher SIGKILL;
ACKs advanced 128 → 129 during publisher loss. Original-label commit, visibility
wait and repeated commit passed. Every field of the final 999,428 active rows
matched on all three fixed replicas; all 330 cumulative source ACKs reconciled.

This demonstrates native sequence protection against stale full-row replay and
these live cancellation retractions. It does not establish hard-delete tombstone
retention across compaction, arbitrary dimension-version merges or end-to-end
exactly-once publication across both tables. They are separate transactions.

## Runbook

Preserve parent source journals, `results/historical/<case>/prepare-*.json`, input
snapshots and the fixture identity. Never reseed or regenerate a label after an
ambiguous publication. Inspect SHOW TRANSACTION for both saved labels, with the
owned fixture unchanged. Restart the retained publisher and use its `commit` mode
with the same RUN_ID/CASE_ID; it sends the existing labels and independently gates
on native VISIBLE. A PRECOMMITTED batch can be completed; an ABORTED/missing label
requires investigation and an explicit decision, not blind replay. Label retention
is finite; this example does not qualify multi-day coordinator outages.

After both transactions settle, run the parent's complete source receipt/body
verifier on every fixed replica, with no missing-version skips or replica fallback.
Only then use the five-command lifecycle's down/docker-down to destroy owned
state. The corrected automated scenario is `make replay-failure` (existing seeded
fixture) or `make test-production`; the initial `make test` seeds a fresh fixture
before running that same scenario.

## Source basis

The pinned Doris 4.1.5 release handler accepts either label or transaction ID and
returns JSON status separately from HTTP 200; it delegates the actual two-phase
operation to the stream-load executor. Source inspected at release commit
`917b20be83878e98f94e3ebf7efccc80346e3e50`:
[StreamLoad2PCAction](https://github.com/apache/doris/blob/917b20be83878e98f94e3ebf7efccc80346e3e50/be/src/service/http/action/stream_load_2pc.cpp).
The [transaction documentation](https://doris.apache.org/docs/4.x/data-operate/transaction/)
separates commit, publication visibility and finite label retention.
