# SeaweedFS

S3 over a separated master, volume and filer architecture with HA metadata.

- [Cluster, recovery and benchmarks](s3-cluster/) — independent live readers/writers, preserved ACK histories, leader/process/disk recovery, rebuilt-copy restart, cold reopen, benchmarks and repeatable five-command teardown/recreation with preserved witnesses.
- [S3 comparison and shared test contract](../object-storage/).
- [Shared filesystem cluster](filesystem-cluster/) — two independent FUSE mounts,
  C++ publication/readback, distributed flock and live recovery after both filers
  are killed. Final readback verifies 161 ACKed bodies and all three native copies;
  EIO/EAGAIN outage symptoms and the initial failed probes remain documented.

Fresh [4.55 + etcd 3.7.4 multipart qualification](s3-cluster/#fresh-multipart-recovery--2026-11-27): gateway loss with retained parts, discarded completion response and abort visibility passed, followed by exact three-copy/body and current-revision metadata checks. Historical benchmarks retain their original versions.

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results.

| Example | Next work |
|---|---|
| [s3-cluster](s3-cluster/README.md#future-work--todo) | Exercise supported volume-server growth/drain and verify actual object placement, retained acknowledgements and native repair. |
| [filesystem-cluster](filesystem-cluster/README.md#limits-and-next-tests) | FUSE lock-owner crash, one-filer routing, metadata quorum/partitions, filesystem-interface disk loss, compaction and independent restore. |
