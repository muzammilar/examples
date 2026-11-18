# Vespa

Website: https://vespa.ai/
GitHub: https://github.com/vespa-engine/vespa

| Example | Purpose |
|---|---|
| [phased-product-search/](phased-product-search/) | Single/three full-copy nodes and 3 × 3 grouped storage, separate control/query roles, four ranking modes and native per-copy recovery checks |

| Benchmark | Setup | Observed dense search, 8 clients |
|---|---|---|
| [Phased retrieval](phased-product-search/#historical-2026-11-15-benchmarks) | 2026-11-15, Apple M4 Pro / native aarch64 Docker; 100,000 products / 1,000 queries; 3 nodes, each 2 CPUs / 4 GiB | 288.33 QPS, p99 66.35 ms, recall@10 0.9974 |
| [Grouped storage sharding](phased-product-search/#native-storage-sharding) | 2026-11-15, same host; 100,000 products; 3 control/query hosts (2 CPUs / 2 GiB each) + 9 content hosts (1 CPU / 1.5 GiB each) | 561.30 QPS, p99 24.31 ms, recall@10 0.9966; 2,960 acknowledged updates, 0 lost |

These are historical 2026-11-15 measurements and lifecycle checks. They do not
qualify the newer controller/workload implementation. The example README records
all ranking modes, budgets, quality and the separately dated recovery backfill.

## Recovery backfill — 2026-11-18

The flat three-host fixture now uses independent Go writers/readers and retained
attempt/ACK/ambiguous histories. Native audits check **300,000 copies and 115.2M
float32 coordinates**, all metadata and post-writer-stop version agreement.
Empty-volume replacement passed with **15,443 new ACK updates**. A second-host
failure while the rebuilt copy stayed online passed with **14,542 new ACKs**;
a native read from that rebuilt member verified a fresh ACK before the other
host returned. All **26 unrelated daemon/supervisor identities** stayed unchanged.
Failed harness attempts remain failed reports, with all their histories retained.
Whole-stack retained reopen also passed with **9,134 new ACKs**. The subsequent
warm dense benchmark reached **303.41 queries/s / p99 61.77 ms** at eight clients,
with zero failures across all 8,000 ranking-mode requests. Application/infrastructure
teardown preserved and hash-verified **526 evidence files**. A fresh-volume
five-command lifecycle and non-controller-host crash then passed: **32,157 ACKs /
35,870 attempts** reconciled, all final native copies agreed, and **zero
acknowledged loss** was detected. Another **191 files** were archived; cleanup left
no owned infrastructure or built tags and preserved all ten recorded foreign
volumes. Updated single/sharded qualification and the broader production matrix
remain unrun; see the [current status](phased-product-search/#current-backfill-status--2026-11-18).

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

Two HTTP 412 conditional-failure responses during the new disk test nevertheless
had their attempted bodies present after repair on all replicas. They remain
non-ACK outcomes; an error response is not proof that no replica applied a write.
See the [observed conditional-update gotcha](phased-product-search/#observed-conditional-update-gotcha).
