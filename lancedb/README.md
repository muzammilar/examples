# LanceDB

Website: https://lancedb.com/
GitHub: https://github.com/lancedb/lancedb

| Example | Purpose |
|---|---|
| [versioned-product-search/](versioned-product-search/) | Single process or one writer + three shared-volume query workers; version pinning, native compaction proof, independent Go traffic and preserved process-recovery witnesses |

| Benchmark | Setup | Observed dense search, 8 clients |
|---|---|---|
| [versioned-product-search](versioned-product-search/#post-maintenance-warm-diagnostics) | 2026-11-19, Apple M4 Pro / native aarch64 Docker; 100,000 products / 1,000 queries; 4 processes, each 2 CPUs / 3 GiB; only 3 readers serve; post-update/maintenance catalog, complete response validation | 244.73 QPS, p99 61.02 ms, recall@10 1.0000 |

Both profiles completed their five-command Docker lifecycle. The example README
contains all retrieval modes, candidate-ranking quality, caps, crash results and
limitations. ANN recall and response projections differ between engines; these
are single warm local measurements, not a normalized performance ranking. The
new cluster recovery backfill reconciled 16,760 ACKs across nine preserved
histories without acknowledged-version loss. Historical fresh-catalog results
remain in the example; do not pool them with the post-update diagnostics.

## Known issues

OSS is an embedded library. These shared-volume readers do not provide database
replication, distributed consensus or automatic writer election. The writer and
shared volume are single failure points. Native Python/Arrow SDKs handle
indexing/version operations; the local HTTP wrapper is a standard-library lab
service. Retained versions and unindexed updates consume additional storage.
