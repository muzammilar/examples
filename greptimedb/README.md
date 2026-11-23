# GreptimeDB

Website: [greptime.com](https://greptime.com/)
GitHub: [GreptimeTeam/greptimedb](https://github.com/GreptimeTeam/greptimedb)

Rust telemetry database with separate query frontends, region-owning datanodes,
Metasrv control plane and configurable WAL/object storage. The examples distinguish
three logical regions per workload table from three durable WAL/object copies: datanode count alone
does not imply three hot replicas of every region.

Each example exposes `telemetry-workload/` through a tracked source symlink. Its
Makefile materializes an ignored `.build/` Docker context before building.
Configuration, dependency definitions, test controllers and results are local to
the example; no sibling example needs to be run first.

| Example | What it shows | Validation |
|---|---|---|
| [Single-node](single-node/) | Durable local WAL; same metrics/logs corpus in GreptimeDB and standalone Victoria backends | Million samples + million logs per backend, full values/body/time readback, query timings and cleanup passed |
| [Clustered telemetry](docker-compose-cluster/) | Three regions, three WAL brokers, three metadata stores and replicated S3; native failover, empty local-disk replacement and 3 → 5 → 3 region migration under writes | Full corpus and 102,500 acknowledged recovery rows passed |
| [Kubernetes operator](kubernetes-operator/) | Official operator on a private kind cluster; native scaling, worker/controller outages and WAL quorum loss | Fresh four-million-record baseline and ten recovery cases passed; 207,000 historical ACKs retained without loss. Earlier 3,400-sample loss, manual repair and bootstrap fix documented |

The isolated [WAL bootstrap reproducer](wal-bootstrap-reproducer/) demonstrates
100/100 acknowledged rows disappearing after restart when metadata continues after
failed topic preparation; the healthy bootstrap control retains 100/100. It also
verifies guard rejection and explicit application repair. This is a failing
correctness reproducer, not another performance ranking.

The [cluster README](docker-compose-cluster/#measured-results) and
[single-node README](single-node/#results) and
[operator README](kubernetes-operator/#measured-results) give measured numbers, exact caps,
versions and limitations. These are bounded synthetic backfills and warm-query
experiments with different dependency/durability costs, not normalized product
capacity rankings.

## Known issues

The examples pin 1.3.1 and use native supported WAL paths. Its configuration marks
object-store WAL experimental/unsupported; distributed recovery uses Kafka-compatible
remote WAL plus S3. Datanode process/disk tests do not qualify metadata-quorum loss,
multiple broker failures, correlated host power loss or backup restore. Local copies
share one physical host. Follow the explicit SQL/pipeline and Compose-volume gotchas
in the cluster README.

The full future [observability comparison](../observability/) adds tracing and
complete Victoria/Grafana/ClickStack/OpenObserve/SigNoz stacks. Today's metrics/logs
baselines cover only a subset of that plan.

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

- [ ] **Planned GreptimeDB versus Victoria comparison:** follow the
  [equal-budget comparison contract](../observability/README.md#greptimedb-vs-victoria-planned-comparison)
  for common metrics/logs, single/batched/native ingestion, cold/warm queries and
  live recovery. Measure whole-stack CPU, memory, compression, storage and disk
  read/write bytes/IOPS alongside latency/throughput. Select performance depth
  first; the existing baselines do not establish a whole-stack winner.

| Example | Next work |
|---|---|
| [docker-compose-cluster](docker-compose-cluster/README.md#future-work--todo) | Bring supported dependency/quorum, partition and rolling-change cases from the operator example into this Compose topology. |
| [kubernetes-operator](kubernetes-operator/README.md#future-work--todo) | Test permanent worker loss/local-PV rescheduling and operator crash during active maintenance. |
| [single-node](single-node/README.md#future-work--todo) | Qualify retained-volume whole-stack shutdown/cold reopen and independent restore, without borrowing clustered recovery claims. |
