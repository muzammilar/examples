# SeaweedFS

S3 over a separated master, volume and filer architecture with HA metadata.

- [Cluster, recovery and benchmarks](s3-cluster/) — independent live readers/writers, preserved ACK histories, leader/process/disk recovery, rebuilt-copy restart, cold reopen, benchmarks and repeatable five-command teardown/recreation with preserved witnesses.
- [S3 comparison and shared test contract](../object-storage/).
