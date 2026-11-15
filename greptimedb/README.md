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
| [Kubernetes operator](kubernetes-operator/) | Official operator on its own kind cluster; owned external durable dependencies, native scaling, rolling resizing, node/controller outages and WAL quorum loss | Latest scale-out/in passed; vertical scale-up failed full readback with 666,600 missing metric samples; cause unresolved and remaining cases pending. Earlier results are explicitly historical |

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
