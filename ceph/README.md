# Ceph

Ceph combines a distributed RADOS storage layer with object (RGW), block (RBD)
and filesystem (CephFS) interfaces. This example focuses on the S3-compatible
RGW interface and native BlueStore recovery.

- [RGW cluster and disk recovery](s3-cluster/) — three Linux guests, nine OSDs,
  CRUSH host placement, independent live S3 writer/reader, preserved ACK histories,
  guest crash, empty-disk rebuild, rebuilt-member crash and proxy outage.
- [S3 comparison](../object-storage/) — shared correctness/benchmark protocol;
  Ceph's emulated timings are kept separate from native-container measurements.
