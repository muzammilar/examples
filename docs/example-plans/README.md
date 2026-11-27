# Example design handoff

Research reviewed on 2026-11-26 against the repository inventory, AGENTS.md and linked upstream documentation. These checklists propose experiments for implementation. Existing measured results remain in their example directories.

| Work | Assignments |
|---|---|
| Relational, analytical and wide-column systems | [Orders, shard moves, corrections and recovery](relational-and-analytical.md) |
| Streaming and observability | [CDC, queues, workflows and telemetry](streaming-and-observability.md) |
| Data services | [Document, search, KV, graph, edge and authorization](data-services.md) |
| Storage and lakehouse | [Catalogs, publication, compaction, fencing and restore](storage-and-lakehouse.md) |
| Existing systems | [Follow-ups using the current labs](existing-systems.md) |

## Give an agent one assignment

Choose an unchecked item and name the system and intended example directory. Read its upstream sources, the nearest README and Makefile, [AGENTS.md](../../AGENTS.md) and [FORMAT.md](../../FORMAT.md). Recheck the current stable release, supported topology, storage requirements and edition before implementation. Source links describe capabilities available when researched. A documentation page or a proposed failure test does not prove a local configuration works.

Start with the named workload and correctness oracle, then implement one initial failure and its recovery. Keep a live writer, independent reader and durable acknowledgement history. Record CPU, memory, compression, disk space and activity alongside operation latency and correctness. Ask for a performance depth before a performance campaign. Expand deployment and failure coverage through the repository's progressive passes.

Each assignment should produce its own five-command lifecycle, local Docker-built workload, explicit fault/recovery commands, compact measured report and remaining TODOs. Use an existing lab for a related workload when its ownership is clear. Give independently runnable projects their own directory and state. Keep raw evidence ignored and preserve failed approaches beside replacements.

## Design corrections to retain

- PostgreSQL, MySQL, StarRocks and several sharding systems already have deployments and measured results. Extend the existing project when the question fits.
- Pinot needs separate upsert and star-tree tables. CDC consumers need an event format that matches the actual producer envelope. A latest-state table feeding an insert-triggered aggregate needs an explicit correction strategy.
- Verify licensing for clustering, native checkpoints, recovery and local trial distributions before choosing a topology. The category “Community” has different limits across projects.
- Match source offsets across table-format comparisons and record each table's commit identity. Match durability and visibility across cache, queue and database comparisons.
- Separate embedded processes, database servers, shared-catalog serving workers, storage replicas and application-managed shards in the deployment description.
- Tigris stays blocked on a supported self-hosted storage server. Hosted observability checks require a separate authorized run. All current implementation assignments use isolated local infrastructure.

New candidates include Accumulo, SolrCloud, ZooKeeper, Fluss, standalone Flink state recovery, Hudi, Ozone, SlateDB and BookKeeper. Their briefs identify specific questions that the existing examples leave open.

The [columnar-format comparison](columnar-formats.md) includes CSV, Parquet, persisted Arrow IPC and Vortex; its runs remain pending.
