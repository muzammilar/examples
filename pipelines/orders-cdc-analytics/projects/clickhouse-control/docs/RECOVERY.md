# Preserved failures and retained recovery

Fixture `04dea8c332d245188d7a7ec3ac30e4bc`, 2026-11-27. Original failed
phase statuses remain failed; recovery resumed the same data rather than reseeding.
Raw evidence stays ignored; the curated report carries checks and phase identities.

| Failure | Symptom and diagnosis | Verified recovery |
|---|---|---|
| Disk preflight | Insufficient Docker free bytes before starting the control | Approved unused build-cache cleanup restored headroom; no data/foreign volumes removed, guard retained |
| SQL portability | Doris rejected reserved alias `current`; its three complete body checks had already passed | Rename alias to `live_orders`, resume the fenced input; original comparison phase remains failed |
| Keeper memory | Multi/Create refused at native soft limit 230.40 MiB with about 245.75 MiB tracked RSS; process remained running, no kernel OOM | Change each Keeper cap 256→512 MiB and each server 3,840→3,584 MiB, preserving the same aggregate 12 GiB. Serial retained recreation restored metadata and all data checks |
| Teardown Python binding | Nested function shadowed imported offset module before archive/drop | Import as `offset_audit`; resume same state, recheck groups/source/bodies, archive original consumed values |
| Retained FE dependency deadlock | Compose waited for FE1 health before starting FE2/FE3, while retained FE1 needed its voting quorum. Native catalog remained UNKNOWN | Start the six already-existing owned FE/BE containers together, wait for native quorum/tablet agreement, then exact three-copy checks. No reset, new voters or source regeneration |

The retained-start helper now validates all six owned container IDs and starts
existing roles without the bootstrap-only Compose dependency ordering. This is
not evidence of a Ceph/storage fault or a Doris data-loss bug. The exact manual
start sequence passed in the retained fixture; a fresh end-to-end automated run
after this final helper change is not claimed.

## Serving-replica crash

`make serving-failure` creates a unique witness case and inserts new eight-row
batches using quorum two and fsync, with deduplication disabled. It SIGKILLs the
owned third ClickHouse replica, proves exit 137 without OOM, waits beyond session
expiry, proves two active replicas on all four tables, then starts the same
retained container. The live reader and external fsynced intent/ACK journal remain
active. Recovery requires all three copies of every acknowledged body and a clean
final analytical oracle. Do not erase volumes or retry with new logical IDs to
hide uncertain outcomes. The measured case had 2,048 ACK rows and no ambiguity.

## Safe cleanup

`make down` verifies the exact final source, fixed backend copies and consumer
fences; exports the contiguous consumed value tape with checksums; captures
resources; then drops only owned application state. `make docker-down` verifies
owned containers, networks and volumes are gone and retires the parent fixture.
Both completed. The tape is evidence, not a restored backup. Keep raw histories
ignored locally; preserve curated negative findings in Git.
