# Retained recovery from writable-volume exhaustion

Observed with Flink 2.3.0, operator 1.16.1 and SeaweedFS 4.55 on 2026-11-26.
The initial storage configuration allowed four volumes at 64 MiB each. A 2 GiB
PVC request and free Docker VM space did not override that application-level cap.

The streaming source/checkpoints exhausted writable volumes at about 258 MiB of
allocated storage. SeaweedFS reported `No writable volumes and no free volumes
left`; S3 PUTs failed, and Flink checkpoint 26 remained in flight. The TCP probe
still reported Ready. A small final dataset does not imply a small checkpoint/
temporary-storage requirement, and a listening port does not prove write capacity.

One TaskManager Pod had already been deleted after a completed checkpoint. Native
Flink observations showed one restoration. This run therefore contains two
mechanisms: worker loss followed by storage-capacity exhaustion.

## Recovery performed

The operator application and its existing output/checkpoints were retained. Only
SeaweedFS's `-volume.max` was changed from 4 to 32; its Deployment restarted on the
same PVC. There was no input reload, output deletion or Flink reset. SeaweedFS
re-elected its single master after restart, and the existing checkpoint completed
with **237,834 ms** native checkpoint duration. The job finished; an independent
S3 reader verified **1,000,000 orders**, **599,500,000 cents** and zero wrong,
missing or duplicate bodies. Observed controller duration was **327.08 seconds**,
which includes manual diagnosis/intervention and is not worker-only failover RTO.

`make storage-expand` exposes the retained capacity-change procedure. The recovery
patch was executed, and the Make command was also checked for idempotence against
an already expanded store. It additionally checks a real S3 write/read/delete
body. This setup probe supplements the Pod's TCP probe; it is not continuous
production-grade capacity monitoring.

## Preserved undersized profile

On a fresh lifecycle, `make up STORAGE_PROFILE=pressure` selects the four-slot
configuration from `deploy/storage/pressure/`. The original attempt used a 2 GiB
PVC request; this reproducible profile uses the standard 4 GiB request while
retaining the actual four-slot limit. The new overlay renders correctly, but
that exact overlay has not been rerun as a second pressure trial.

Observe native SeaweedFS allocation errors and checkpoint age, then use
`make storage-expand` without resetting the job. `make test` observes native
completion and independently verifies the result. Keep failed histories before
any later teardown. The normal profile allows 32 slots and requests a fresh 4 GiB
PVC; its complete million-order run passed without injected faults.

Logical deletion of checkpoint objects is not the same as physical volume
reclamation. Compaction/vacuum, a full physical disk, slow I/O and permanent storage
loss remain distinct unrun cases. This one-Pod dependency does not supply HA or an
independent backup. Do not generalize its limits to the dedicated replicated
SeaweedFS example.

[Curated outcomes/resources](../reports/INITIAL_RESULTS.json)
· [SeaweedFS volume growth source](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/topology/volume_growth.go)
