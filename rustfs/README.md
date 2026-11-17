# RustFS

S3 with native distributed erasure coding, protected shard integrity and disk healing.

- [Cluster, recovery and benchmarks](s3-cluster/) — independent live writer/reader,
  retained ACK histories, separate process/disk/router faults, native EC2+2
  recovery, fresh lifecycles and measured benchmarks. Extended operational gaps
  and the native inline-payload proof boundary are explicit in the example.
- [S3 comparison and shared test contract](../object-storage/).
