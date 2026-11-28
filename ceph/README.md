# Ceph

Ceph combines a distributed RADOS storage layer with object (RGW), block (RBD)
and filesystem (CephFS) interfaces. Separate examples exercise S3-compatible
RGW recovery and native CephFS metadata-server recovery.

- [RGW cluster and disk recovery](s3-cluster/) — three Linux guests, nine OSDs,
  CRUSH host placement, independent live S3 writer/reader, preserved ACK histories,
  guest crash, empty-disk rebuild, rebuilt-member crash and proxy outage.
- [S3 comparison](../object-storage/) — shared correctness/benchmark protocol;
  Ceph's emulated timings are kept separate from native-container measurements.
- [CephFS metadata HA](filesystem-ha/) — Umbrella 21.2.0, three Linux guests,
  nine OSDs, active/standby MDS and a native C++ writer/reader with external ACK
  reconciliation. Native-library guest/disk recovery and fresh-cluster file restore
  passed 1,788 ACK files / 117,178,368 bytes. Long guest-outage stalls, PG-count
  drift and the failed retained-setup keyring case remain documented.

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results.

| Example | Next work |
|---|---|
| [s3-cluster](s3-cluster/README.md#future-work--todo) | Isolate individual MON, MGR, OSD and RGW daemon failures instead of inferring them from whole-guest outages. |
| [filesystem-ha](filesystem-ha/README.md#deployment-limits-and-next-tests) | PG creation drift, post-replacement boot, partitions, mounted-client semantics, MDS scaling and online/full-metadata restore. |

The CephFS lab also contains a [kernel-client access-path comparison](filesystem-ha/projects/kernel-client/)
with guest-only secure mounts and remount recovery. These clients share the
parent lab’s lifecycle; no host kernel mount or cloud storage is used.
