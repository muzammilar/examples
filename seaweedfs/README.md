# SeaweedFS

S3 over a separated master, volume and filer architecture with HA metadata.

- [Cluster, recovery and benchmarks](s3-cluster/) — independent live readers/writers, preserved ACK histories, leader/process/disk recovery, rebuilt-copy restart, cold reopen, benchmarks and repeatable five-command teardown/recreation with preserved witnesses.
- [S3 comparison and shared test contract](../object-storage/).

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

| Example | Next work |
|---|---|
| [s3-cluster](s3-cluster/README.md#future-work--todo) | Exercise supported volume-server growth/drain and verify actual object placement, retained acknowledgements and native repair. |
