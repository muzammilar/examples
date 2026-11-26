# Streaming, workflows and observability

These are proposed experiments. Start with one workload and one failure per system. Reuse the logical input from the linked examples in each lab's own deployment. The [repository requirements](../../AGENTS.md) cover measurement, isolation, performance-depth selection and later recovery passes.

## Time-series

- [ ] **TDengine — fleet sensors across vgroups.** Give each device a subtable, use tags for site/model and send late readings to one busy site.
  On a Compose cluster, record the vgroup placements and replication setting, stop a data node hosting a leader, then compare per-device timestamps, values and per-site aggregates against the input. Reuse the [fleet workload](../../timescaledb/fleet-telemetry/). [TDengine architecture](https://docs.tdengine.com/internals/arch/) · [Availability options](https://docs.tdengine.com/operations-and-tooling/operations/ha/)
- [ ] **Apache IoTDB — sparse measurements from a factory.** In a ConfigNode/DataNode cluster, write time-aligned gauges plus occasional missing readings, then backfill one device family.
  Stop a DataNode owning a region, inspect region membership after repair, and verify every timestamp, value and gap against the original series. [Data models](https://iotdb.apache.org/UserGuide/latest-Table/Background-knowledge/Common-Concepts_apache.html) · [Region management](https://iotdb.apache.org/UserGuide/latest-Table/User-Manual/Load-Balance_apache.html)
- [ ] **DolphinDB — quote at trade time.** Use an eligible local single-node build to as-of join every trade with the most recent quote for the same symbol.
  Include a trade before the first quote, identical timestamps with a declared tie rule, and late quotes. Reopen the persisted inputs and compare trade-by-trade with an offline sorted reference and the [QuestDB market corpus](../../questdb/market-data/). [As-of join semantics](https://docs.dolphindb.com/en/Programming/SQLStatements/TableJoiners/asofjoin.html) · [Local setup and license](https://docs.dolphindb.com/en/getting_started.html)
- [ ] **ArcticDB — point-in-time market-data corrections.** Append bars to versioned symbols, snapshot the closing book, then correct an old symbol/day.
  Reopen through a fresh process after an interrupted update and compare both the latest and pinned books with independent frames. Run LMDB and an example-owned S3-compatible endpoint separately with one writer per symbol. [Storage layout](https://docs.arcticdb.io/latest/technical/on_disk_storage/) · [Writer coordination](https://docs.arcticdb.io/6.14.0/faq/)

The VictoriaMetrics cluster and GreptimeDB comparison are specified under [observability](#observability). The existing [TimescaleDB](../../timescaledb/fleet-telemetry/), [QuestDB](../../questdb/market-data/), and [InfluxDB](../../influxdb/iot-fleet/) examples supply comparable fleet or market data.

## Streaming SQL and CDC

- [ ] **Materialize — changing a dimension after a sale.** Replay the [RisingWave order changes](../../risingwave/cdc-order-metrics/) into a joined materialized view, moving a customer to another region after checkout.
  Verify every affected group against PostgreSQL at the same settled input frontier. Use the emulator for SQL development and the documented local kind deployment for replica/restart tests, subject to its license key and storage prerequisites. [Self-managed installation](https://materialize.com/docs/self-managed-deployments/installation/install/) · [Emulator](https://materialize.com/blog/materialize-emulator/)
- [ ] **Feldera — retract a result after a correction.** Feed inserts, cancellations and customer-region changes into an incremental SQL order ledger, including a correction that removes an entire group.
  Compare the signed output changes and final view with batch SQL. In Community, rebuild from retained input after stopping the pipeline and measure replay work. Native checkpoint/resume and fault-tolerance tests require Enterprise. [Checkpoint and edition boundary](https://docs.feldera.com/pipelines/fault-tolerance/) · [Kafka inputs](https://docs.feldera.com/connectors/sources/kafka/)
- [ ] **Timeplus Proton — late events and idle windows.** Replay timestamped sensor pulses from Kafka, leave one window empty and send boundary records before and after the configured watermark.
  Advance event time explicitly, replay the same offsets into a fresh view, and compare emitted windows and revisions with an event-time reference under the declared lateness rule. [Tumbling windows](https://docs.timeplus.com/tumble-aggregation) · [Emit policies](https://docs.timeplus.com/streaming-aggregations)
- [ ] **PostgreSQL → Debezium → Kafka → ClickHouse — snapshot-to-WAL handoff.** Mutate and delete rows while the initial snapshot runs, stop Connect after a published batch, and resume using its preserved offsets and slot.
  Compare full latest rows and deletions against a source watermark after catch-up, and account for duplicate events and retained WAL separately. [PostgreSQL connector](https://debezium.io/documentation/reference/stable/connectors/postgresql.html)
- [ ] **MySQL → Debezium → Kafka → Flink → ClickHouse — corrected order margins.** Preserve the Debezium envelope through Flink, process insert/update/delete retractions, and sink keyed, versioned results with deletion markers.
  Crash between sink acceptance and offset/checkpoint completion, then compare live orders, cancelled orders and daily totals at a settled watermark with MySQL. Recomputing totals from latest rows supplies the baseline. [Flink Debezium format](https://nightlies.apache.org/flink/flink-docs-stable/docs/connectors/table/formats/debezium/) · [ClickHouse incremental view semantics](https://clickhouse.com/docs/materialized-view/incremental-materialized-view)
- [ ] **Airflow — restartable CDC backfill.** Extend the [existing scheduler lab](../../airflow/basic/) with a DAG that records a bounded source cut, requests a Debezium incremental snapshot and reconciles the result.
  Restart the scheduler or retry the task after submission and verify that the same backfill ID ends with one reconciliation and complete source rows. The continuous connector and processor run independently of the DAG. [Airflow DAGs](https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/dags.html) · [Debezium snapshots](https://debezium.io/documentation/reference/stable/connectors/mysql.html)
- [ ] **Apache Flink — recover keyed state during rescaling.** Assign a versioned count and event-time timer to each account.
  Stop a TaskManager during a checkpoint, then separately rescale from a savepoint. Compare fired timers, state and committed output per account at a fixed source offset with uninterrupted replay using the same sink guarantee. [Flink state and checkpoints](https://nightlies.apache.org/flink/flink-docs-stable/docs/concepts/stateful-stream-processing/)
- [ ] **Apache Fluss — current inventory and its change history.** Write stock corrections to a primary-key table and have a second consumer reconstruct inventory from its changelog.
  Stop a bucket leader under declared acknowledgement and minimum in-sync replica settings, then check point lookups and reconstructed state against the writer's receipts. Set log retention longer than the consumer outage and test expiry separately. [Tables and retention](https://fluss.apache.org/docs/table-design/overview/) · [Bucket replication](https://fluss.apache.org/docs/concepts/architecture/)

## Message streaming and queues

- [ ] **Apache Iggy — ordered replay after a primary change.** Write per-customer sequences to multiple partitions, record producer confirmations and commit a consumer offset before stopping the partition primary.
  On the supported three-node profile, resume from the saved offset and compare gaps, duplicates and per-partition order with a single-node restart control. [Cluster implementation and release](https://iggy.apache.org/blogs/2026/09/21/release-0.9.0/) · [Partition leadership](https://iggy.apache.org/docs/clustering/multi-leader/)
- [ ] **Fluvio — filtered replay with a Rust/Wasm SmartModule.** Keep an input topic of keyed device events and transform a consumer's stream into actionable alerts.
  Restart a consumer and then an SPU that led a replicated partition. Verify raw offsets and filtered alert IDs against the same predicate offline, including the case where no input qualifies. [Filter-map operator](https://www.fluvio.io/docs/latest/smartmodules/features/operators/filter-map/) · [SPU replication](https://www.fluvio.io/docs/latest/fluvio/concepts/architecture/spu/)
- [ ] **Apache Pulsar — slow-subscriber replay after offload.** Keep a durable subscription behind while a producer rolls ledgers, then read an offloaded range from a supported local tier after confirming the original ledgers were removed from BookKeeper.
  Interrupt a broker and a bookie separately in a clustered profile, reconcile message bodies and subscription positions, and state the configured ensemble/write/ack quorums. [Pulsar architecture](https://pulsar.apache.org/docs/next/concepts-architecture-overview/) · [Local filesystem offloader](https://pulsar.apache.org/docs/next/tiered-storage-filesystem/)
- [ ] **RabbitMQ — competing delivery versus repeatable replay.** Send the same jobs to a replicated quorum queue and a replicated stream.
  Crash a consumer after recording a side effect but before acknowledgement/offset storage, then lose one broker. Check unique job effects, redeliveries, publisher confirms and whether a second reader can replay old jobs. A classic queue is an optional single-copy control. [Streams](https://www.rabbitmq.com/docs/streams) · [Partition behaviour](https://www.rabbitmq.com/docs/partitions)
- [ ] **Aeron Cluster — deterministic order book.** Use three cluster members, an upstream-supported clustered service and client, and an independent book of accepted commands.
  Take a service snapshot, kill the leader with later orders in the log, and reconcile sequence, balances and fills after election and replay. Time plain transport separately from committed service responses. [Cluster architecture](https://aeron.io/docs/aeron-cluster/overview/) · [Upstream implementations](https://github.com/aeron-io/aeron)
- [ ] **KurrentDB — optimistic inventory reservations.** Race two reservations at the same stream revision.
  Restart a persistent subscriber after it applies an event but before it acknowledges it, then compare rebuilt inventory and duplicate attempts with the accepted event stream. Free single-node covers this experiment. From 26.2 multi-node failover requires an Enterprise license. [Persistent subscriptions](https://docs.kurrent.io/server/v26.2/features/persistent-subscriptions) · [Edition boundary](https://kurrentdb.kurrent.io/blog/licensing-in-kurrentdb-v26-2-and-beyond-what-s-free-and-what-s-licensed/)

## Durable execution

Use a local fake payment service with a durable charge ledger and a deterministic order ID. Record attempts, committed side effects and workflow completions separately.

- [ ] **Temporal — activity response lost after charge.** Kill a Go worker after the payment service commits and before activity completion reaches Temporal.
  Deliver a signal while the worker is unavailable, then compare recovery history and final order state with the charge ledger. Include a receiver without deduplication as a duplicate-charge control. [Activity execution and idempotency](https://docs.temporal.io/activity-definition)
- [ ] **Restate — one reservation per inventory object.** Send concurrent commands for one SKU to a virtual object and drive two external payment attempts with a stable idempotency key.
  Interrupt a durable step after the charge commits, restore the service, and compare the object's stock, journaled step result and external charges. [Durable steps and retries](https://docs.restate.dev/develop/go/durable-steps)
- [ ] **Self-hosted Inngest — recover a partially completed fan-out.** Run the server with local PostgreSQL and Redis, send duplicate order events and stop a worker midway through its per-line steps.
  Reconcile committed line items, retried steps, completed function runs and queue backlog, then restart the server separately with retained state. [Self-hosted persistence and queue options](https://www.inngest.com/docs/self-hosting) · [Step retries](https://www.inngest.com/docs/durable-execution/guides-and-advanced/error-handling/retries)
- [ ] **Hatchet — fan-in after a failing child.** Submit one shipment with independent stock-check tasks and a dispatch task that needs all checks.
  Crash the worker owning one child, then compare parent/child outcomes, repeated attempts and actual dispatches with the dependency graph and an external receipt. [Task retries and durable tasks](https://docs.hatchet.run/reference/go/runnables)
- [ ] **DBOS — transfer surviving a lost response.** Commit a debit, credit and receipt as one supported local PostgreSQL transaction.
  Stop the workflow process at the response boundary and compare recovery with an external HTTP step whose endpoint may already have committed. Check balances, receipt identities and actual effects per workflow ID. [Transactions and datasources](https://docs.dbos.dev/golang/tutorials/transaction-tutorial) · [Concurrent executions](https://docs.dbos.dev/explanations/concurrent-executions)
- [ ] **Temporal / Restate / Inngest — same failed order.** Run reserve → charge → ship with the same fault immediately after charging and a second scenario that refuses shipment and requires compensation.
  Compare attempts and external net charges per order, recovery work and time to final state with the same idempotency policy. Reuse the individual cases above before any performance comparison.

## Observability

The [existing comparison contract](../../observability/README.md#greptimedb-vs-victoria-planned-comparison) fixes data, durability, whole-stack/per-role budgets, admission versus visibility, single versus batch and cold versus warm reads. Extend the measured [GreptimeDB/Victoria baselines](../../greptimedb/docker-compose-cluster/) without turning their unequal budgets into a ranking.

- [ ] **GreptimeDB vs Victoria — first matched comparison.** Run metrics and logs first on identically sized local machines or sequentially on the same reserved machine.
  Preserve metric timestamps/labels/values and log IDs/bodies in an external corpus, interrupt each stack's declared durable write path, and verify data again after catch-up. Add traces when both sides expose the same contract. [Greptime remote WAL](https://docs.greptime.com/user-guide/deployments-administration/wal/remote-wal/configuration/) · [VictoriaMetrics replication](https://docs.victoriametrics.com/victoriametrics/cluster-victoriametrics/)
- [ ] **VictoriaMetrics — replicated metrics and duplicate scrapes.** Use vminsert/vmstorage/vmselect and stagger two HA scraper feeds.
  Remove one storage member during ingest, then compare sample values and missing intervals across the outage under explicit replication, dedup interval and partial-response settings. [Cluster and deduplication](https://docs.victoriametrics.com/victoriametrics/cluster-victoriametrics/)
- [ ] **VictoriaLogs — durable shipping across two targets.** Feed tenant-tagged immutable log bodies via vlagent to two independent VictoriaLogs deployments.
  Take one destination offline, measure buffer growth, and reconcile each destination's recovered bodies including duplicates after reconnect. The shipper and buffer belong in the HA budget. [Cluster replication design](https://docs.victoriametrics.com/victorialogs/cluster/)
- [ ] **VictoriaTraces — span replication and partial traces.** Send trace families with known parent/child IDs to two independent destinations through the collector.
  Stop one destination midway through a trace, recover it, and query each destination independently for missing children, duplicate spans and cross-tenant leakage. [VictoriaTraces cluster and external replication](https://docs.victoriametrics.com/victoriatraces/cluster/)
- [ ] **Prometheus — a missed scrape versus a lost sample.** Let two scrapers watch incrementing counters and a gauge, then disconnect one scraper for two intervals.
  Compare raw samples with successful scrapes, and range-query results with the declared evaluation grid. Count observations never scraped separately from samples lost after persistence. [Local TSDB and remote interfaces](https://prometheus.io/docs/prometheus/latest/storage/) · [Range functions](https://prometheus.io/docs/prometheus/latest/querying/functions/)
- [ ] **Loki — retained chunks and tenant isolation.** Ingest identical text into two tenants with overlapping labels and unique event IDs.
  Interrupt the ingester and compactor separately, query both fresh and flushed ranges, then verify which exact events expire and whether their chunks are reclaimed after the configured delay. [Components](https://grafana.com/docs/loki/latest/get-started/components/) · [Compactor retention](https://grafana.com/docs/loki/latest/operations/storage/retention/)
- [ ] **Tempo — a trace across live and historical storage.** Use deterministic traces with delayed last spans.
  In Tempo 3 microservices mode interrupt the local Kafka-compatible queue and then a block-builder, and compare live-store reads with object-backed historical reads by complete span/parent IDs. Use monolithic mode as a separate baseline with its own write path. [Tempo architecture](https://grafana.com/docs/tempo/latest/introduction/architecture/)
- [ ] **Grafana Mimir — recent versus historical metric availability.** In the ingest-storage profile, break the local Kafka path after remote-write admission, then stop a store-gateway during a historical query.
  Verify current and historical sample sets, partial responses, and backlog catch-up separately. Record which component owns the acknowledgement. [Mimir architecture](https://grafana.com/docs/mimir/latest/references/architecture/)
- [ ] **Cortex — replicated recent samples versus persisted blocks.** Write to two tenants and temporarily remove enough ingesters to breach the selected write quorum.
  Validate expected rejections, then compare recovered WAL samples and older blocks with the timestamp/value corpus and equivalent Mimir queries. [Cortex architecture](https://cortexmetrics.io/docs/architecture/)
- [ ] **Thanos — deduplication with an HA scrape gap.** Pair two independent Prometheus replicas with distinct replica labels and local object storage.
  Stop one replica, later stop a Store Gateway, and compare deduplicated and raw series across the recent-to-block boundary, including partial-response warnings. [Thanos Query deduplication](https://thanos.io/tip/components/query.md/)
- [ ] **Jaeger — trace continuity across collector and storage restarts.** Use a local supported persistent backend such as OpenSearch.
  Stop ingestion between parent and child spans and compare trace-ID retrieval with the external span tree after replay. Hold sampling fixed for the matched Tempo/VictoriaTraces subset. [Supported Jaeger storage backends](https://www.jaegertracing.io/docs/latest/storage/)
- [ ] **OpenObserve — accepted logs before object flush.** Compare single-node persistence with an HA profile that includes the NATS coordinator, PostgreSQL and local object store.
  Stop an ingester before and after object flush. Check exactly which acknowledged bodies survive retained versus replaced storage and whether the same tenant queries agree after compaction. [OpenObserve data path](https://openobserve.ai/docs/architecture/)
- [ ] **SigNoz — explain a slow service request.** Generate a trace with one slow database span, linked logs and a known service metric.
  Interrupt the collector and ClickHouse separately, and verify raw signal completeness as well as the trace-to-log and service-level result exposed to an operator. [SigNoz architecture](https://signoz.io/docs/architecture/)
- [ ] **ClickStack — find a tenant's failed request.** Inject a traceable error in one of two tenants and search by its high-cardinality request ID.
  Interrupt the collector and then the UI settings store separately, and compare persisted event bodies, discoverability and saved investigation state after recovery. [ClickStack components and deployment modes](https://clickhouse.com/docs/clickstack/architecture)
- [ ] **Three-stack incident replay.** Feed the same instrumented request/worker corpus separately to GreptimeDB, the Victoria stack and Prometheus + Loki + Tempo/Grafana.
  Verify the exact failing request, trace tree, eligible log IDs and metric buckets after one dependency failure. Extend to ClickStack, OpenObserve and SigNoz on the matching signal/query subset once this control works. [Shared experiment contract](../../observability/README.md#shared-experiment-contract)
- [ ] **OpenTelemetry Collector → ClickHouse.** Force a recoverable exporter outage while writing uniquely identified telemetry.
  Compare memory and persistent sending queues across a collector restart, then exceed the declared queue capacity in a separate run. Reconcile partial acceptance, rejected data and duplicate sink rows from saved IDs. [Collector resiliency](https://opentelemetry.io/docs/collector/resiliency/) · [ClickHouse exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/clickhouseexporter)
- [ ] **Vector — disk-buffered log shipping.** Feed numbered records from a source whose acknowledgement semantics are documented.
  Stop the ClickHouse or Quickwit sink, restart Vector with its buffer retained, then replay the same inputs to compare delivered bodies, duplicates and drops with the declared source/sink guarantee. [Vector delivery guarantees](https://vector.dev/docs/architecture/guarantees/)
- [ ] **Akvorado — missing flow records and templates.** Generate synthetic IPFIX templates/data with known sequences and an sFlow stream with explicit sampling.
  Drop one template and separately pause Kafka consumption. Check which flow bodies decode, sequence gaps and corrected byte totals, accounting for sampling and UDP loss separately. [Akvorado pipeline](https://akvorado.net/docs/intro)
- [ ] **Nagios Core — transient versus confirmed host failures.** Replay a one-check timeout and a sustained service outage.
  Record the independent fault times, soft/hard state transitions, notifications and recovery notifications to verify retry and alert policy. [Nagios state types](https://assets.nagios.com/downloads/nagioscore/docs/nagioscore/4/en/statetypes.html)
- [ ] **Zabbix — disconnected branch office.** Disconnect a proxy from its server while agents keep reporting, then reconnect before and after the configured offline-buffer horizon in separate runs.
  Diff individual timestamps and alarm/recovery transitions against the same fleet and schedule used for Nagios. [Proxy buffering](https://www.zabbix.com/documentation/current/en/manual/concepts/proxy/distributed_monitoring)

## Hosted comparisons on hold

These cases require separate authorization and verified access. Keep the account reminder in the [hosted comparison plan](../../observability/README.md#hosted-comparisons-only-where-free-access-is-available). They have no local execution step.

- [ ] **Honeycomb — locate the slow request.** After account setup and a quota check, use known trace IDs and independent span trees to test how well a sampled dataset identifies a single slow tenant.
  Compare retrieved fields and missing traces with the sampling decision. [Honeycomb editions](https://www.honeycomb.io/pricing)
- [ ] **Better Stack — incident visibility within quota.** For a future authorized hosted run, submit synthetic logs with known request IDs and compare accepted, queryable and retained bodies across a declared time window.
  Record which signal and retention limits the chosen plan applies. [Better Stack plans](https://betterstack.com/pricing)
- [ ] **Chronosphere / Cortex XCOR — cardinality shaping.** With separately confirmed no-cost access, aggregate ephemeral device labels into a site total.
  Compare the preserved total and lost label-level detail against the raw series. The self-hosted Cortex metrics project above is a separate system. [Vendor shaping documentation](https://docs.chronosphere.io/control/shaping)
