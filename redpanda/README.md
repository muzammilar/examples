# Redpanda

Website: https://www.redpanda.com/ · GitHub: https://github.com/redpanda-data/redpanda

Kafka-API-compatible streaming broker in C++ (Seastar, thread-per-core). Raft per partition,
no ZooKeeper/KRaft; Schema Registry, HTTP Proxy and the Admin API are built into the broker.

| folder | what |
|---|---|
| [`single-node/`](single-node) | One broker (`dev-container` mode) + Redpanda Console: `rpk` topics and groups, Schema Registry (Avro, compatibility check), HTTP Proxy, Console API; `rpk` produce/consume benchmark. |

| [`docker-compose-cluster/`](docker-compose-cluster) | Three brokers + Console; RF=3 topics; `make failover` stops a partition leader under acks=all load (Go, franz-go) and reads every acknowledged record back; `make scale-demo`: 3 → 5 → 3 brokers (`rpk cluster brokers decommission`) under load. |

| [`kubernetes-operator/`](kubernetes-operator) | Redpanda Operator 26.2.4 (Helm) on kind, a `Redpanda` resource with 3 brokers (one per worker); `make failover` force-deletes a broker pod under an acks=all rpk producer and checks every acked record. |

## License

- The core is source-available under the
  [Business Source License 1.1](https://github.com/redpanda-data/redpanda/blob/dev/licenses/bsl.md)
  (Community Edition): any use except offering Redpanda as a commercial streaming or queuing
  service to third parties. Each release becomes Apache 2.0 four years after its release date.
  The enterprise-feature code is under the
  [Redpanda Community License](https://github.com/redpanda-data/redpanda/blob/dev/licenses/rcl.md)
  and needs a paid key ([licenses/README.md](https://github.com/redpanda-data/redpanda/blob/dev/licenses/README.md)).
- Enterprise features need a license key
  ([overview](https://docs.redpanda.com/current/get-started/licensing/overview/)). A new cluster
  gets a built-in 30-day trial; when it ends, inactive enterprise features are disabled and active
  ones go into a restricted state. Self-managed Enterprise-only: Tiered Storage, Remote Read
  Replicas, Topic Recovery, Whole Cluster Restore, Continuous Data Balancing, continuous
  intra-broker (core) balancing, Leader Pinning, Audit Logging, RBAC, group-based access control,
  OIDC/OAUTHBEARER and Kerberos authentication, Schema Registry authorization, server-side
  schema ID validation, FIPS mode, Iceberg Topics, Cloud Topics, Shadowing, fetch read coalescing,
  topic deletion control. Console Enterprise-only: SSO (OIDC/OAuth), Console RBAC, debug bundles,
  partition reassignment in the UI.


- The Redpanda Operator is free to run; it gates Redpanda Connect pipelines and multi-cluster
  (stretch) deployments on a license.
- Everything in these examples runs without a key. During the trial, `rpk cluster license info`
  lists `core_balancing_continuous` and `partition_auto_balancing_continuous` as in use: they are
  on by default until the trial expires, then the balancer falls back to `node_add`.

## Benchmark

| example | date | setup | result |
|---|---|---|---|
| [single-node](single-node/README.md#benchmark) | 2026-10-04 | 1 broker capped 2 CPUs / 3 GB, one `rpk` producer then consumer, 1M x 1000 B, acks=all | produce 152,161 records/s (145.1 MiB/s); consume 881,057 records/s (840.2 MiB/s) |

| [docker-compose-cluster](docker-compose-cluster/README.md#failover) | 2026-10-04 | 3 brokers (`--smp 1`), 5,000 x 512 B records/s for 60 s, acks=all, idempotent; leader of partition 0 stopped at 15 s, restarted at ~35 s | 299,950 acked, 0 failed, 0 lost, 0 duplicates; affected partitions stalled up to 10.0 s (franz-go `MetadataMinAge` 5 s) / 5.5 s (500 ms); rejoin 4.5-15.5 s |
| [docker-compose-cluster](docker-compose-cluster/README.md#scaling-3--5--3-brokers) | 2026-10-04 | 3 → 5 → 3 brokers under 2,000 x 512 B records/s, acks=all, 24 partitions RF 3 | scale-out balanced in 81 s (29 of 72 replicas moved), decommission of 2 in 16 s; 405,300 acked, 0 failed, 0 lost; slowest ack 5.2 s |

| [kubernetes-operator](kubernetes-operator/README.md#failover) | 2026-10-04 | 3 brokers on kind (1 core / 2Gi each), rpk producer 500 records/s, pod `redpanda-2` force-deleted | 30,000 of 30,000 acked records read back, 0 producer errors; pod back and cluster healthy in 23 s |

Apple M4 Pro, Docker VM aarch64, Redpanda v26.2.3 (native arm64 image). Single runs on a shared
Docker VM.

## Known issues

Seen with Redpanda v26.2.3 / Console v3.12.0, 2026-10-04.

- `rpk registry schema check-compatibility` requires `--schema-version` (`latest`).
- `--mode dev-container` bypasses fsync and turns on write caching (`rpk redpanda start --mode help`);
  don't take durability or latency numbers from it.

- A `docker stop`ped broker does not transfer leadership first: its partitions are leaderless for
  ~3-5 s until a Raft election (`leadership transfer: false` in the logs). The docs drain a broker
  with maintenance mode before a planned restart (not run here).
- `rpk cluster health` exits 10 when the cluster is unhealthy.
- The partition balancer does not place replicas on brokers whose disk is over 80% used
  (`partition_autobalancing_max_disk_usage_percent`). On Docker Desktop all brokers share the VM
  disk; when that is fuller than 80%, new brokers get nothing.
- `--mode dev-container` bypasses fsync and turns on write caching; don't take durability or
  latency numbers from it.

- The operator's `Redpanda` resource reports `Ready` before every broker pod is Ready; wait on
  the pods too.
- `--mode dev-container` bypasses fsync and turns on write caching (`rpk redpanda start --mode help`);
  don't take durability or latency numbers from it.
- The trial license makes a fresh cluster behave differently from one 30 days old (continuous
  balancing on, then off). Pin the behaviour with `rpk cluster config set partition_autobalancing_mode node_add`
  if a test depends on it.
