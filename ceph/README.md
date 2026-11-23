# Ceph

Ceph combines a distributed RADOS storage layer with object (RGW), block (RBD)
and filesystem (CephFS) interfaces. This example focuses on the S3-compatible
RGW interface and native BlueStore recovery.

- [RGW cluster and disk recovery](s3-cluster/) — three Linux guests, nine OSDs,
  CRUSH host placement, independent live S3 writer/reader, preserved ACK histories,
  guest crash, empty-disk rebuild, rebuilt-member crash and proxy outage.
- [S3 comparison](../object-storage/) — shared correctness/benchmark protocol;
  Ceph's emulated timings are kept separate from native-container measurements.

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

| Example | Next work |
|---|---|
| [s3-cluster](s3-cluster/README.md#future-work--todo) | Isolate individual MON, MGR, OSD and RGW daemon failures instead of inferring them from whole-guest outages. |
