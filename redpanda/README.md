# Redpanda

Website: https://www.redpanda.com/ · GitHub: https://github.com/redpanda-data/redpanda

Kafka-API-compatible streaming broker in C++ (Seastar, thread-per-core). Raft per partition,
no ZooKeeper/KRaft; Schema Registry, HTTP Proxy and the Admin API are built into the broker.

| folder | what |
|---|---|
| [`single-node/`](single-node) | One broker (`dev-container` mode) + Redpanda Console: `rpk` topics and groups, Schema Registry (Avro, compatibility check), HTTP Proxy, Console API; `rpk` produce/consume benchmark. |

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
- Everything in these examples runs without a key. During the trial, `rpk cluster license info`
  lists `core_balancing_continuous` and `partition_auto_balancing_continuous` as in use: they are
  on by default until the trial expires, then the balancer falls back to `node_add`.

## Benchmark

| example | date | setup | result |
|---|---|---|---|
| [single-node](single-node/README.md#benchmark) | 2026-10-04 | 1 broker capped 2 CPUs / 3 GB, one `rpk` producer then consumer, 1M x 1000 B, acks=all | produce 152,161 records/s (145.1 MiB/s); consume 881,057 records/s (840.2 MiB/s) |

Apple M4 Pro, Docker VM aarch64, Redpanda v26.2.3 (native arm64 image). Single runs on a shared
Docker VM.

## Known issues

Seen with Redpanda v26.2.3 / Console v3.12.0, 2026-10-04.

- `rpk registry schema check-compatibility` requires `--schema-version` (`latest`).
- `--mode dev-container` bypasses fsync and turns on write caching (`rpk redpanda start --mode help`);
  don't take durability or latency numbers from it.
- The trial license makes a fresh cluster behave differently from one 30 days old (continuous
  balancing on, then off). Pin the behaviour with `rpk cluster config set partition_autobalancing_mode node_add`
  if a test depends on it.
