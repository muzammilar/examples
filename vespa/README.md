# Vespa

Website: https://vespa.ai/
GitHub: https://github.com/vespa-engine/vespa

| Example | Purpose |
|---|---|
| [phased-product-search/](phased-product-search/) | Single/three-content-node profiles, three physical copies, four ranking profiles, controller/node loss and native per-copy metadata checks |

| Benchmark | Setup | Observed dense search, 8 clients |
|---|---|---|
| [Phased retrieval](phased-product-search/#benchmark) | 2026-11-15, Apple M4 Pro / native aarch64 Docker; 100,000 products / 1,000 queries; 3 nodes, each 2 CPUs / 4 GiB | 288.33 QPS, p99 66.35 ms, recall@10 0.9974 |

Both profiles completed their five-command Docker lifecycle. Every physical copy
was checked by native replica-directed reads after the cluster fault. The
example README records all ranking modes, CPU/RAM caps, candidate quality and
recovery results.

## Known issues

All control/query/content roles share three local containers. Losing the active
controller's node invokes native election and first-state safety waits: the first
resumed write ACK was 122.82 s after SIGKILL, with 0 acknowledged loss. The test
keeps that node down for 180 s and continues requests with bounded deadlines.
Flat-group ANN query fan-out consumes replicated index work; upstream recommends
grouped distribution for ANN read scaling. A grouped-topology comparison remains
future work. The pinned image's native CLI needs explicit UTF-8 output to preserve
non-ASCII text in verification exports. Docker disk feed guards remain enabled.
