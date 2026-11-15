# Typesense

Website: https://typesense.org/
GitHub: https://github.com/typesense/typesense

| Example | Purpose |
|---|---|
| [product-search/](product-search/) | Single/three full Raft replicas, plus three application-managed shards with three replicas each; routing, search and leader loss |

| Benchmark | Setup | Observed dense search, 8 clients |
|---|---|---|
| [product-search](product-search/#benchmark) | 2026-11-15, Apple M4 Pro / native aarch64 Docker; 100,000 products / 1,000 queries; 3 servers, each 2 CPUs / 2 GiB | 1,084.10 QPS, p99 12.22 ms, recall@10 0.9888 |
| [application-managed shards](product-search/#application-managed-shards) | 2026-11-15, same host; 100,000 products / 100 customers; 3 independent groups × 3 replicas, each 2 CPUs / 2 GiB | Global dense: 351.25 QPS, p99 39.51 ms, recall@10 0.9955; customer dense: 392.82 QPS, p99 32.57 ms, sampled recall@10 1.0000 |

All three profiles completed their five-command Docker lifecycle. The example README
contains all retrieval modes, candidate-ranking quality, caps, crash results and
limitations. ANN recall and response projections differ between engines; these
are local workload measurements, not a normalized performance ranking.

## Known issues

Raft replicas each store their group's complete dataset. `MODE=sharded` uses three
independent groups and application-managed tenant routing, not native Typesense
sharding. Migration/rebalancing and global text/hybrid ranking are not implemented.
Follower collection/document visibility can lag imports; the focused test waits
for every replica. The example adds curl to the official image for readiness.
