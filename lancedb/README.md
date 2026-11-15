# LanceDB

Website: https://lancedb.com/
GitHub: https://github.com/lancedb/lancedb

| Example | Purpose |
|---|---|
| [versioned-product-search/](versioned-product-search/) | Single process or one writer + three shared-volume query workers; version pinning, compaction and separate process-loss tests |

| Benchmark | Setup | Observed dense search, 8 clients |
|---|---|---|
| [versioned-product-search](versioned-product-search/#benchmark) | 2026-11-15, Apple M4 Pro / native aarch64 Docker; 100,000 products / 1,000 queries; 4 processes, each 2 CPUs / 3 GiB; only 3 readers serve the cluster benchmark | 734.25 QPS, p99 15.57 ms, recall@10 0.9490 |

Both profiles completed their five-command Docker lifecycle. The example README
contains all retrieval modes, candidate-ranking quality, caps, crash results and
limitations. ANN recall and response projections differ between engines; these
are local workload measurements, not a normalized performance ranking.

## Known issues

OSS is an embedded library. These shared-volume readers do not provide database
replication, distributed consensus or automatic writer election. The writer and
shared volume are single failure points. Native Python/Arrow SDKs handle
indexing/version operations; the local HTTP wrapper is a standard-library lab
service. Retained versions and unindexed updates consume additional storage.
