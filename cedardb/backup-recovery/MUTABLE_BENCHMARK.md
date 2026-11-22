# Sustained mutable-order analytical comparison

Single-node, warm-query, ticker-paced one-writer/one-reader HTAP; not saturation, arbitrary changing aggregates, serializability, independent-host durability or a universal ranking. Each cell restarts its database process and creates a fresh schema; base volumes/OS caches remain. Median of independent trial summaries, not pooled percentiles. CPU includes observers and excludes unsampled edges; native peaks cover full process lifetime. Native block counters are per-device VM/cgroup accounting, not host physical IO; do not sum duplicate device layers or Docker/native counters. Physical disk IO latency/queue depth unavailable.

This cohort does not issue explicit ANALYZE after copying each mutable table; native automatic statistics preparation remains enabled. Optional read-only catalog/EXPLAIN observations are retained per case with timestamp and administrative-client costs. The first PostgreSQL live cell briefly used additional 1-CPU/512-MiB one-shot observation clients; their actual CPU/RSS/cgroup costs are preserved in those observations. Later psql probes execute inside the existing database cap. Unobserved cases have no direct readiness proof. Results describe this preparation profile, not a comparison of optimally prepared engines.

1,000,000 rows; 3 fresh-process trials per cell; 60 s warmup and ≥300 s monotonic measurement. 4 CPUs / 6 GiB per database; 1 CPU / 512 MiB per actor, swap disabled. The inactive database is stopped.

| Backend | Live mutations/readers | Queries/s median (min … max) | Trial p50 / p99 median | DB sampled CPU/query | DB sampled mean memory median |
|---|---|---|---|---|---|
| cedar | False | 36.310 (35.628 … 36.625) | 27.396 / 29.254 ms | 0.104950 core-s | 1505.74 MiB |
| cedar | True | 33.961 (33.839 … 34.108) | 30.453 / 33.025 ms | 0.115666 core-s | 1540.59 MiB |
| postgres | False | 11.042 (11.020 … 11.109) | 90.985 / 113.202 ms | 0.361228 core-s | 1067.31 MiB |
| postgres | True | 11.224 (11.217 … 11.369) | 90.118 / 103.932 ms | 0.355462 core-s | 1080.86 MiB |

Full per-trial counts, complete body/receipt reconciliation, query and separate successful/error mutation/read latency, native CPU/RSS/charged-memory, compression, whole-store disk allocation and per-device byte/IOPS rates are in [MUTABLE_BENCHMARK_RESULTS.json](MUTABLE_BENCHMARK_RESULTS.json). Sampling is approximately once per second plus sequential probes; actual intervals are retained. Application lifetime native counters include warmup and shutdown; measurement counters are separate. Native compression and verified CSV gzip are different layers. Nanosecond timers do not imply nanosecond accuracy. See [the workload/contract and failure history](MUTABLE.md).

The immutable base CSV is **274,666,817 bytes**, SHA-256 `8e3eb0cc5a03c0fb2c5c2d5a218895b779628c245af6394a3bdac8a13de70941` on both backends. Mutable exports and their checksums are separate per trial. Data has 10,000 customers, 100 aggregate groups, non-null typed numeric columns and 256-byte string payloads; generation and mutation formulas are in the checked-in contract.

| Backend / live / trial | Device | Read / write MiB | Read / write IOPS | Whole store logical / allocated MiB | Export gzip1 ratio |
|---|---|---|---|---|---|
| cedar / False / 1 | 254:0 | 0.000 / 0.000 | 0.000 / 0.000 | 204.249 / 1670.793 | 4.531× |
| postgres / False / 1 | 254:0 | 0.000 / 82.949 | 0.000 / 1.146 | 1526.773 / 1526.840 | 4.531× |
| cedar / True / 1 | 254:0 | 0.000 / 297.965 | 0.000 / 304.430 | 493.067 / 1927.500 | 4.528× |
| postgres / True / 1 | 254:0 | 0.000 / 90.957 | 0.000 / 51.576 | 1534.703 / 1534.777 | 4.528× |
| postgres / True / 2 | 254:0 | 0.211 / 91.285 | 0.023 / 51.588 | 1534.734 / 1534.816 | 4.528× |
| cedar / True / 2 | 254:0 | 1.625 / 298.457 | 0.087 / 304.489 | 499.669 / 1933.383 | 4.528× |
| postgres / False / 2 | 254:0 | 0.000 / 81.137 | 0.000 / 1.122 | 1527.015 / 1527.094 | 4.531× |
| cedar / False / 2 | 254:0 | 0.000 / 0.000 | 0.000 / 0.000 | 212.996 / 1679.023 | 4.531× |
| cedar / False / 3 | 254:0 | 0.000 / 0.000 | 0.000 / 0.000 | 213.107 / 1679.445 | 4.531× |
| postgres / False / 3 | 254:0 | 0.000 / 82.668 | 0.000 / 1.132 | 1527.023 / 1527.102 | 4.531× |
| cedar / True / 3 | 254:0 | 4.500 / 297.406 | 0.241 / 303.982 | 500.780 / 1936.129 | 4.528× |
| postgres / True / 3 | 254:0 | 0.000 / 92.191 | 0.000 / 51.613 | 1534.882 / 1534.961 | 4.528× |

Block byte deltas and IOPS cover the observed measurement intervals, not seed/export work. Whole-store bytes are a post-verification snapshot including the immutable base, mutable tables, receipts, indexes, WAL and unreclaimed engine files. The independent CSV compression ratio is not native engine compression. Native table encoding/compression snapshots are retained in JSON. Application per-role CPU, memory and native I/O observations are also retained; absent device counters are unavailable, not zero. The configured whole-stack budget is 5 CPUs / 6.5 GiB alone or 7 CPUs / 7.5 GiB with all three workload roles, excluding Docker VM/host/controller costs.
