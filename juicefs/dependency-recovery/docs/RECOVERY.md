# Recovery findings and runbook

## Metadata loss with retained disk

The sole PostgreSQL metadata process was killed for 15.189 seconds. Writers and
warm historical readers stalled; no ACK advanced during the observed outage.
Restarting the same owned container/storage recovered progress without reseeding.
All 640 ACK bodies and 1,920 native copies matched afterward. A successful write
reached 16.318 seconds and historical body read 15.503 seconds despite low p99s.
This demonstrates retained-process recovery, not metadata HA or permanent disk loss.

Run `make metadata-outage` on an initialized fixture. Keep the witness and native
volume state. If interrupted, inspect owned service identity and native logs,
restart the same metadata service, wait for mount progress, then `make verify`.
Never format again or clear metadata to restore apparent health. Missing ACK data
remains a failure requiring a real independently tested restore procedure.

## Object gateway outage and ambiguous publication

`make object-store-outage` creates a new third FUSE mount before the fault and
checks independent mount identity. The final qualified case held the gateway down
20.186 seconds with its IP stable. Warm historical reads continued correctly;
the previously unread mount returned EIO. One fsync returned EIO after 21.038
seconds, leaving an empty unpublished staging file. 511 other live publications
completed, and all 639 fixture ACK bodies matched all three owners afterward.

Restore the retained gateway, preserve error records, and run `make verify`.
Reconcile final and temporary names against the durable witness and exact payload
oracle. An unacknowledged final body may have committed; absence of ACK is not
absence of effects. Do not rename/replay an ambiguous file to make counts match.
The audit checks nonempty ambiguous bodies independently and classifies empty
staging explicitly. Wrong/short successful reads are hard failures, not retries.

## Failed probes remain failed

1. The first S3 case passed independent data reconciliation but failed its harness
   guard: Docker CLI short IDs were compared to native full IDs. Canonical native
   identities and a regression test fix the guard. The original failed result is
   retained, not retroactively relabeled passing.
2. The repeat reused a mount warmed by recovery verification. Correct reads during
   S3 loss therefore failed cold-cache qualification. A manual refresh after the
   probe had already finished also changed network endpoint allocation. This run
   is confounded and does not establish a single-fault recovery guarantee.
3. That repeat's gateway moved from 172.19.0.14 to 172.19.0.17 while the old address
   was assigned to the replacement mount. Native writer errors continued dialing
   the old address after gateway recovery. Pinned `pkg/object/restful.go` creates
   `dnscache.New(time.Minute)` and its transport resolves through that cache.
   This supports cached-endpoint involvement; isolated IP-change testing remains
   unrun. The observed ACK gap was 66.515 seconds, with 11 ambiguous publications.
4. The old cold-probe report field `completed_after_restore` described when the
   controller joined the process, not when the native failure occurred. Original
   raw evidence stays unchanged; current code uses `joined_after_restore` and
   native event timestamps. The final 319-ms cold failure interval starts at the
   process header, not immediately before a timed read call.

The first fixture ultimately retained 1,652 ACKs and 12 empty unacknowledged
staging files; all 4,956 native copies of ACK bodies matched. The corrected case
ran in a new, explicitly separate fixture only after preserving and destroying
that first fixture. No failed fixture was silently reset in place.

## Evidence preservation and cleanup

A fenced first-fixture logical tar was body-verified against the witness before
cleanup: 433,920,000 bytes, SHA256
`6bc870d6dff51683cd40dbeb25e3817400f9e3474b6ecc20e06e31e61309705d`.
It contains 1,652 ACK files, 12 empty staging files and the empty lock file.
Its metadata dump SHA256 is
`76c1ae70be7bb7fb24c5d199d62a2cc1bf924b122385adfbf27eac6a5c172cb4`.
This logical evidence copy excludes unreferenced object bodies and has not been
restored into a fresh filesystem. Do not call it a qualified independent backup.
An earlier copy failed because the adapter expected native file type `file`;
JuiceFS dumps `regular`. That failed copy attempt is preserved too.

The first teardown's mount wrappers exited 143 after successful unmount, without
OOM or SIGKILL. Explicit TERM cleanup/exit handling corrected the wrapper; the
fresh final teardown observed every persistent role exit 0. Both fixtures removed
only label-checked owned containers, networks and volumes. Raw JSONL, native logs,
archives and failed probes remain ignored locally; curated outcomes are tracked.

## Next pass, not part of this level-3 result

- Independently change the S3 gateway IP with a fresh client-cold mount and no
  simultaneous topology changes; measure resolver/connection recovery explicitly.
- Metadata failover with a supported HA deployment, separate from object loss.
- Fresh-cluster restore with metadata and referenced object versions, then exact
  witness reconciliation; quantify RPO and orphan handling.
- Trash expiration, physical object reclamation and maintenance interference.
- Independent-host/CSI placement, partitions, disk loss and physical durability.

A stable gateway frontend is a production design consideration, not a tested HA
result here. Client memory caching, object replication and a logical archive each
serve different purposes; none replaces metadata redundancy and tested recovery.
