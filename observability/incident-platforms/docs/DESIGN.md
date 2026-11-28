# Bounded platform comparison

## Deployment and source decisions

**Official:** ClickStack OSS 2.47.0 uses its native collector, combined app/API,
MongoDB and ClickHouse. Preserve its supported schema/bootstrap/OpAMP path rather
than substituting an arbitrary stock collector and calling it ClickStack. Disable
usage exports and expose only example-owned local endpoints. The official Compose
file pins older MongoDB/ClickHouse dependencies: verify a supported current patch
or document a concrete compatibility exception before choosing the lab images.
[Deployment](https://clickhouse.com/docs/clickstack/deployment/docker-compose),
[pinned Compose](https://github.com/ClickHouse/ClickStack/blob/%40hyperdx/app%402.47.0/docker-compose.yml).

**Official:** SigNoz Community 0.152.0 uses Foundry rather than the deprecated
repository-bundled Compose installation. Its local topology includes PostgreSQL
metadata, Keeper, ClickHouse, native collector and SigNoz API/UI. Inspect Foundry
0.5.0's rendered templates but pin every image instead of retaining `latest`.
The current collector release inspected is 0.144.19. Preserve its schema migration
sequence and native histogram helper; build/download pinned public artifacts
before joining the isolated runtime network. Do not execute an installer into HOME.
[Installation](https://signoz.io/docs/install/docker/),
[Foundry templates](https://github.com/SigNoz/foundry/tree/v0.5.0/docs/examples/docker/compose).

**Official:** OpenObserve OSS 1.2.0 can use SQLite plus local disk in single-node
mode. Its HA architecture instead needs NATS, PostgreSQL and object storage; do
not disguise the local profile as that deployment. Check the OSS edition and
native OTLP/authentication/SQL APIs, disable telemetry, and retain native schema
mapping in the result rather than normalizing away fields that disappeared.
[Architecture](https://openobserve.ai/docs/architecture/).

**Derived:** The Grafana reference includes the actual Grafana UI/API as well as
Prometheus, Loki, Tempo and collector. All roles count toward its new whole-platform
budget. The smaller three-signal lab's measurements cannot substitute for this
profile. Reuse native configuration concepts and the tracked deterministic corpus;
never depend on its generated state or invoke its Makefile.

All five inspected application/collector images advertise native ARM64 manifests.
Image pulls, startup, effective configuration and supported API behavior still
need local verification; manifest availability is not a passing deployment.

## Level-3 execution budget

Per native profile: one small driver control, one full healthy corpus, one held
10-second ingress crash with a 120-second continuity deadline, then full ACK
reconciliation. Each full publication uses 1,000 batches per signal and a 200 ms
post-batch delay (roughly four minutes plus native response/readback time). Permit
one separately named durability reference when the native default fails; do not
expand into a soak, saturation or broad failure matrix. Query screening is three
fresh-process trials with bounded warm repeats after correctness/recovery gates.
A failed prerequisite remains a failed result and blocks comparative ranking.

## Stages and bounded experiments

1. Pin the native contract and effective configuration. Start with a small three-
   signal fixture; inspect the created schema and query every selected field.
   Preserve numeric type/string projections, timestamp precision, absent fields
   and unsupported queries explicitly. Keep an exact type verdict separate from
   a clearly labeled logical-value comparison; a count is insufficient.
2. Run one healthy full corpus per platform. Read back all acknowledged values
   through an independent native API/oracle, including incident tenant selection,
   error counts, metric values and trace/log correlation. Keep source admission,
   visibility and durable persistence separate. Run product investigation queries
   separately from raw storage readback and record unavailable product APIs.
3. Keep writers/readers live while SIGKILLing the native ingestion collector for
   ten seconds; restart with its retained storage. Prove the fault and recovered
   queue identity, then fence and reconcile every ACK and ambiguous request.
   Source replay is a separately witnessed repair, never a way to erase loss.
4. After correctness/recovery gates, use three fresh-process first-query trials
   and bounded warm repeats for the same incident queries. State engine/OS cache
   retention; no unsupported claim of physical-cold storage. These are practical
   timings, not a capacity or long-tail ranking. Do not add a broad matrix/soak.

Record CPU utilization/core-seconds, mean/peak memory, allocated/logical storage,
source wire versus native storage compression, disk read/write bytes and available
I/O counters, network, failures, completeness, admission latency and recovery time.
Unavailable device/native metrics stay unavailable; budgets include every service.

## Important ingestion boundary

Foundry's native collector template batches before exporters and disables exporter
sending queues. An OTLP ACK can therefore precede downstream durable storage.
A separately durable upstream queue cannot repair that boundary if it discards its
copy on the collector's earlier ACK. Inspect and measure the actual native path;
do not describe it as durable merely because a queue or batch processor exists.
Document supported persistent-buffer configurations and any measured loss before
considering an additional bounded remedy. Preserve all original failed evidence.

**Official/derived:** Keep native ClickHouse insert batching/schema behavior visible.
Per `insert-batch-size` and `decision-ingestion-strategy`, source OTLP batch size is
not necessarily the ClickHouse INSERT batch size. Observe actual parts and native
exporter batches; avoid tiny synchronous inserts or an unexplained global async-
insert change. Use the supported native product schema and read it back before
query design. [Insert strategy](https://clickhouse.com/docs/best-practices/selecting-an-insert-strategy).

## Failure-domain comparison boundary

OpenObserve's local mode has no separate ingestion collector: ingress, storage
and API/UI share the monolith. A native ingress-process outage there interrupts
reads as well as writes; it has a larger blast radius than killing a separate
ClickStack/SigNoz/Grafana collector. Record the actual component and availability
scope. Compare surviving ACKs and verified recovery, but never rank those recovery
times as equivalent isolated-collector faults. Do not add a synthetic gateway just
to hide that architectural difference.
