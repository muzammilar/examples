# Typesense

Website: https://typesense.org/
GitHub: https://github.com/typesense/typesense

| Example | Purpose |
|---|---|
| [product-search/](product-search/) | Single/three full Raft replicas, plus three application-managed shards with three replicas each; routing, search, independent live traffic, role crashes and empty-volume rebuild |

| Benchmark | Setup | Observed dense search, 8 clients |
|---|---|---|
| [single-node baseline](product-search/#current-single-node-baseline) | 2026-11-17, same host/corpus; 1 server, 2 CPUs / 2 GiB | 794.44 QPS, p99 42.39 ms, recall@10 0.9890 |
| [product-search](product-search/#current-retained-cluster-benchmark) | 2026-11-17, Apple M4 Pro / native aarch64 Docker; 100,000 products / 1,000 queries; 3 servers, each 2 CPUs / 2 GiB | 1,085.56 QPS, p99 12.24 ms, recall@10 0.9879 |
| [application-managed shards](product-search/#current-application-sharded-recovery) | 2026-11-17, same host; 100,000 products / 100 customers; 3 independent groups × 3 replicas, each 2 CPUs / 2 GiB | Global dense: 359.11 QPS, p99 37.76 ms, recall@10 0.9957; customer dense: 440.97 QPS, p99 30.14 ms, sampled recall@10 1.0000 |

The 2026-11-15 profiles completed their then-current five-command Docker lifecycle.
The 2026-11-17 backfill has qualified independent live writers/readers, retained
ACK histories, follower/leader crashes, empty-volume rebuild and rebuilt-member
crash in the three-node profile. Retained setup/correctness/report and a fresh
five-command lifecycle passed, preserving 158 archived evidence hashes and ten
unrelated volume identities. Whole-stack reopen exceeded its health budget;
later same-volume reconciliation recovered every acknowledged value. The nine-node
backfill passed 12 core faults plus an extra different-member failure after rebuild,
all-copy/vector proofs, benchmark and a fresh lifecycle (296 archived evidence
hashes unchanged). The updated single-node lifecycle, persistence/reopen
(106.85 s), correctness and benchmark also passed. The example README
contains all retrieval modes, candidate-ranking quality, caps, crash results and
limitations. ANN recall and response projections differ between engines; these
are local workload measurements, not a normalized performance ranking.

## Known issues

Raft replicas each store their group's complete dataset. `MODE=sharded` uses three
independent groups and application-managed tenant routing, not native Typesense
sharding. Migration/rebalancing and global text/hybrid ranking are not implemented.
Leader crashes were independently run on all three application shards with
full metadata/ACK reconciliation across all nine servers.
A retained default-native-readiness run failed a short dense-search check during
recovery; its failed status and ACKs remain in the history. Read lag 10 passed the
four core three-node fault cases. Read lag zero was also tested and can reject
reads under continuing writes even with empty indexing queues. This does not
establish linearizable reads/searches. Full-copy checks read every metadata field
and vector coordinate from each node URL; this is local API proof, not independent
RocksDB decoding.

Follower collection/document visibility can lag imports; the focused test waits
for every replica. The example adds curl to the official image for readiness.
