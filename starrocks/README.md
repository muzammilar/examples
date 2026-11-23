# StarRocks

Website: [starrocks.io](https://www.starrocks.io/)  
GitHub: [StarRocks/starrocks](https://github.com/StarRocks/starrocks)

Columnar analytics with separate FE metadata/query planning and BE storage/compute.
The workload uses mutable order state, stale version replay and dimension joins;
it checks complete values and compares the same history with ClickHouse.

| Example | What it shows | Status |
|---|---|---|
| [Single-copy](single-node/) | One FE + one BE, 1,000,000 orders, upserts/stale replay; one-copy ClickHouse baseline | Full lifecycle, correctness, benchmark and cleanup passed |
| [Compose cluster](docker-compose-cluster/) | 3 FE / 3 BE; 3 buckets × 3 copies, vs ClickHouse 3 shards × 3 copies; live readers/writers through crashes and empty-disk repair | Eight recovery cases passed; 373,000 acknowledged recovery rows across both systems, zero loss |
| [Kubernetes operator](kubernetes-operator/) | Official operator, separate kind cluster/context, scaling and failure experiments | 13 operational cases passed; 1,047,000 historical ACKs reconciled without loss; full lifecycle/cleanup passed |

Each example exposes meaningful local source links and builds from an ignored
materialized `.build/` context. Configuration, dependency definitions, lifecycle
and results belong to that example. Materialized files are never committed.

## Benchmark summary

2026-11-15, Apple M4 Pro, native aarch64 Docker. These are bounded end-to-end
measurements, not capacity rankings. Budgets, methods and limits are in each README.

| Example | Initial StarRocks / ClickHouse ingestion | Regional join p99 SR / CH |
|---|---|---|
| [Single-copy](single-node/#benchmark) | 96,291.9 rows/s / 104,120.5 rows/s | 98.659 ms / 309.203 ms |
| [Equal-budget cluster](docker-compose-cluster/#benchmark) | 85,913.5 rows/s / 51,310.3 rows/s | 184.561 ms / 285.788 ms |
| [Operator](kubernetes-operator/#measured-results) | 72,061.4 rows/s / no CH run | 156.029 ms / no CH run |

## Known issues

Native membership can lag HTTP readiness. Metadata-normal replicas can still lack
a rebuilt tablet; check physical copies. Retained FE quorum startup must not wait
for one isolated peer to become healthy first. Small ClickHouse containers and
many tiny inserts can exhaust memory. Native XOR32 checksums and single-host labs
have explicit verification limits; see the failed-run notes in the cluster README.

## Future work / TODO

Deferred follow-ups are linked below. Each example keeps its concrete checklist
beside the measured results; experiments remain paused.

| Example | Next work |
|---|---|
| [docker-compose-cluster](docker-compose-cluster/README.md#future-work--todo) | Exercise supported FE/BE growth and safe shrink with native tablet draining and complete acknowledged-body checks. |
| [kubernetes-operator](kubernetes-operator/README.md#future-work--todo) | Qualify pinned supported database/operator/CRD upgrades. |
| [single-node](single-node/README.md#future-work--todo) | Qualify graceful stop/start and forced cold reopen with retained data and full acknowledged-body readback. |
