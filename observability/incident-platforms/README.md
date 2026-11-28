# Shared incident: ClickStack, OpenObserve, SigNoz and Grafana

Implementation in progress, **performance level 3**. OpenObserve native controls and a buffered crash
case have run; no cross-platform winner is qualified. The earlier [three-signal lab](../three-signal-incident/) qualifies
a different set of backend paths and does not qualify these products.

Use the same deterministic checkout corpus: **10,000 requests, 1 million gauge
points, 1 million correlated logs and 30,000 spans**. Investigate the same payment
incident: 250 failed requests, 500 error spans and 25,000 error logs, with tenant
skew, trace trees, typed attributes, events and links checked independently.

The local Compose comparison will run one platform at a time. Each profile must
include its native collector, metadata, coordination, storage and API/UI services
within the same **8 CPU / 12 GiB** backend budget; workload actors get another
1 CPU / 512 MiB. These are local single-host deployments, not HA qualification.
No real AWS configuration, hosted exports, MinIO or external accounts are used.

- `docs/DESIGN.md`: native deployment choices, correctness boundaries and bounded
  failure/query plan.
- [`docker-compose/`](docker-compose/): example-owned lifecycle, native WAL
  variants, workload, isolated network, data and curated results.
- [`docs/FINDINGS.md`](docs/FINDINGS.md): measured healthy and failed outcomes,
  including the buffered configuration's **30 missing acknowledged spans**.

Only compact curated evidence will be committed. Raw evidence and materialized
build contexts remain ignored. The Compose README documents the five
lifecycle commands and failed-data preservation guards. ClickStack, SigNoz and
the new whole-platform Grafana profile remain unrun; their comparison is pending.
