# Vespa

Website: https://vespa.ai/
GitHub: https://github.com/vespa-engine/vespa

| Example | Purpose |
|---|---|
| [phased-product-search/](phased-product-search/) | Single/three full-copy nodes and 3 × 3 grouped storage, separate control/query roles, four ranking modes and native per-copy recovery checks |

| Benchmark | Setup | Observed dense search, 8 clients |
|---|---|---|
| [Phased retrieval](phased-product-search/#benchmark) | 2026-11-15, Apple M4 Pro / native aarch64 Docker; 100,000 products / 1,000 queries; 3 nodes, each 2 CPUs / 4 GiB | 288.33 QPS, p99 66.35 ms, recall@10 0.9974 |
| [Grouped storage sharding](phased-product-search/#native-storage-sharding) | 2026-11-15, same host; 100,000 products; 3 control/query hosts (2 CPUs / 2 GiB each) + 9 content hosts (1 CPU / 1.5 GiB each) | 561.30 QPS, p99 24.31 ms, recall@10 0.9966; 2,960 acknowledged updates, 0 lost |

All three profiles completed their five-command Docker lifecycle. Every physical copy
was checked by native replica-directed reads after the cluster fault. The
example README records all ranking modes, CPU/RAM caps, candidate quality and
recovery results.

## Known issues

The default replicated profile combines control/query/content roles in three
containers. Its active-controller host loss invokes native election and
first-state safety waits: first resumed ACK **122.82 s** after SIGKILL. The
sharded profile separates those roles and tests a content-only crash; these
exercise different failure paths. Both preserved all acknowledged versions.

All hosts still share one Docker VM. The sharded profile uses a documented
startup-memory-floor override for small separated lab hosts; real memory limits
and native disk/memory feed guards remain enabled. Resource ceilings differ
between profiles, so the measured throughput ratio is not normalized scaling.
Native CLI verification explicitly sets UTF-8 to preserve non-ASCII product text.
