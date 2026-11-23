# Qdrant TurboQuant: tenant retrieval and disk recovery

A three-peer Qdrant **1.18.3** cluster compares float32 RAM, float32 on disk,
scalar int8 and TurboQuant 4-bit vectors. All four collections use the same
**100,000 points × 384 dimensions**, 100 customers, three explicit customer shard
keys and **three copies per shard**. Each server has 1 CPU / 2 GiB; the benchmark
container has 2 CPUs / 2 GiB. No host ports are published.

## Quick start

Prerequisites: Docker Compose, Make and Python 3. Allow at least 10 GiB Docker RAM
and 10 GiB free Docker disk, plus room for downloaded images.

1. `make docker-up` — build the Go workload/server probe and start three peers.
2. `make up` — create collections, load identical vectors and wait for indexing.
3. `make test` — check every original vector, payload and customer shard on every copy.
4. `make down` — delete workload collections and record cleanup.
5. `make docker-down` — remove only owned services, volumes and local images.

Use `make up POINTS=100000 DIMENSIONS=384`; retain these same variables for all
subsequent commands. `QUERIES=100 HNSW_EF=128 CONCURRENCY=4 DURATION=10` control
the benchmark. Each duration applies to one search variant. Results are local,
ignored files under `results/`; a clean lifecycle archives earlier results.

## What it measures

`make benchmark` reports recall@10 against an independent exhaustive Go dot-product
oracle, serial p50/p99 latency and separate concurrent QPS. It validates native
exact search against that oracle. Queries cover all baseline points and a single
customer. Float vectors are generated deterministically with clusters, anisotropy
and a shared bias, normalized in the application and stored using Dot distance.
This is a synthetic numerical corpus, not evidence of semantic relevance quality.

Float-disk, scalar8 and turbo4 retain original vectors on disk; scalar/turbo
compressed vectors stay in RAM. Float-ram is an additional practical hot control.
Compressed search runs without rescoring, then with original-vector rescoring and
2×/4× oversampling. HNSW uses M=16 / ef_construct=128. Selective customer queries
may use a plain scan; faster filtered queries do not establish quantizer speedups.
Per-collection native memory reports distinguish heap, cache and disk components,
summed across replicas. Four-bit codes do **not** mean an eightfold reduction in
whole-process RSS: original vectors, indexes, payloads and replicas still exist.

## Recovery experiments

After setup, `make test-production` runs independent cases serially:

| Target | Intended injection and recovery |
|---|---|
| `make failover` | Discover and SIGKILL the actual Raft leader, hold it down 30 seconds, restart its retained volume. |
| `make write-leader-failure` | Discover the highest active replica peer ID used by Medium write ordering; crash/rejoin it. |
| `make disk-failure` | Fence q2, delete its label-verified volume, drop lost replicas/peer through native APIs, join a new peer identity and snapshot-replicate all shards. |
| `make failover FAULT_SERVICE=q1` | Crash a different peer after disk rebuild to exercise the recovered copies. |
| `make recover` | Resume an interrupted native empty-disk replacement from the retained ownership marker. |

An independent reader checks known vector bodies and tenant-filtered ANN while a
writer appends new TurboQuant points. Writes use wait=true, Medium ordering and
write consistency 2; point reads use quorum. Neither mutation replay nor weaker
consistency masks errors. The writer fsyncs attempted/acknowledged/ambiguous IDs
outside the database volumes; recovery checks all historical acknowledged IDs,
complete vectors and payloads on every **local physical shard**, without forwarding
the read to another replica. Baseline placement is 34,000 / 33,000 / 33,000 points
per customer group on each replica. Final full verification checks all four
collections. Phase reports separate successful/failed request latency and counts.
A frozen acknowledged-ID barrier proves catch-up while new writes continue.

## Measured results

2026-11-15, Apple M4 Pro, native ARM64 Docker, shared 11-CPU / 24.42 GiB VM.
No host isolation was enforced against unrelated Docker workloads. All profiles
coexist on the same three peers; tests run serially. The 400,000 baseline vectors
were checked in full on all **36 local shard copies**, with zero wrong vectors,
payloads or customer routing. The full lifecycle and owned cleanup passed.

Fresh-fixture loading includes vector generation, transport and journal fsync;
index-ready time additionally includes optimizer convergence. Each profile sends
300 shard-specific requests and finishes with 100,000 indexed vectors.

| Profile | Ingest points/s | Ready after (s) |
|---|---:|---:|
| float-ram | 11,797.6 | 44.618 |
| float-disk | 11,891.2 | 42.560 |
| scalar8 | 10,006.2 | 30.176 |
| turbo4 | 10,153.8 | 25.985 |

Exact-oracle validation is a separate preflight, followed by one warmup and 100
serial measured queries per variant; p50/p99 use upper observed ranks; p99 is the maximum
of this 100-query sample. QPS is a separate four-concurrent-request, 10-second
wall-clock interval. Every response is checked for count, unique IDs, payload SHA,
finite scores and tenant isolation; all 16 intervals had zero errors. Recall uses
an independent float64 dot-product top-10; native exact scores/boundaries must
agree within 1e-5. These are warm, bounded application/HTTP timings, not cold-cache
or sustained capacity measurements. Most of this corpus fits the page cache.

| Profile / search | Filter | Recall@10 | p50 / p99 ms | QPS |
|---|---|---:|---:|---:|
| float-ram / hnsw | all-base | 1.000 | 5.654 / 47.553 | 134.1 |
| float-ram / hnsw | one-customer | 1.000 | 3.351 / 45.020 | 151.4 |
| float-disk / hnsw | all-base | 1.000 | 5.155 / 46.879 | 131.9 |
| float-disk / hnsw | one-customer | 1.000 | 3.565 / 45.967 | 156.9 |
| scalar8 / compressed | all-base | 0.623 | 4.095 / 45.057 | 138.6 |
| scalar8 / rescore-2x | all-base | 0.994 | 4.502 / 44.701 | 150.6 |
| scalar8 / rescore-4x | all-base | 0.997 | 4.409 / 45.793 | 150.4 |
| scalar8 / compressed | one-customer | 0.893 | 3.329 / 45.355 | 149.8 |
| scalar8 / rescore-2x | one-customer | 0.999 | 3.143 / 48.935 | 136.9 |
| scalar8 / rescore-4x | one-customer | 1.000 | 3.342 / 45.879 | 159.8 |
| turbo4 / compressed | all-base | 0.733 | 3.588 / 44.984 | 147.3 |
| turbo4 / rescore-2x | all-base | 0.997 | 3.768 / 45.020 | 143.8 |
| turbo4 / rescore-4x | all-base | 1.000 | 3.562 / 46.175 | 148.7 |
| turbo4 / compressed | one-customer | 0.880 | 3.282 / 49.238 | 150.3 |
| turbo4 / rescore-2x | one-customer | 1.000 | 3.370 / 45.893 | 155.4 |
| turbo4 / rescore-4x | one-customer | 1.000 | 3.480 / 46.914 | 154.9 |

TurboQuant 4-bit needed original-vector rescoring to reach full recall on the
unfiltered synthetic sample; raw compressed search traded recall for its smaller
codes. This is a numerical-corpus observation, not a semantic-quality ranking.
HNSW construction and approximate search can vary between fresh fixtures.

Native collection totals below include **all three copies** and all reported
components, after warm queries. Heap, cached file pages, expected cache and on-disk
bytes are distinct; the heap column alone is not total memory or process RSS.

| Profile | Heap MiB | Cached pages MiB | Expected cache MiB | Disk MiB |
|---|---:|---:|---:|---:|
| float-ram | 33.63 | 628.00 | 614.29 | 1414.58 |
| float-disk | 33.63 | 491.50 | 20.31 | 1269.09 |
| scalar8 | 144.63 | 491.50 | 20.07 | 1379.86 |
| turbo4 | 90.97 | 491.61 | 14.10 | 1320.21 |

The recovery fixture is separate from the fresh benchmark fixture. It retained
20,000 attempted points: **19,766 acknowledged and stored**, with 234 points in
seven ambiguous requests absent after recovery. Unacknowledged absence is not
confirmed acknowledged data loss. Full vector/payload/routing reconciliation on
every replica found **zero missing ACKs, wrong values or unexpected points**;
the final full baseline check passed again after all four cases.

| Case / target | Recovery including 30 s hold | ACK points in case | Ambiguous requests | Read errors | Missing/wrong known reads |
|---|---:|---:|---:|---:|---|
| disk / q2 | 67.000 s | 6,700 | 3 | 0 | 0 / 0 |
| process / q0 | 42.664 s | 4,066 | 1 | 1 | 0 / 0 |
| process / q1 | 44.313 s | 4,600 | 0 | 0 | 0 / 0 |
| write-leader / q2 | 42.779 s | 4,400 | 3 | 0 | 0 / 0 |

Recovery timing includes native API polling, transfer/catch-up and the frozen ACK
barrier, not an exact election instant. Every case required new writes and reads
after convergence. Empty-disk q2 received a new native peer identity and rebuilt
all three shards in all four collections; the following q1 crash passed. Disk
replacement and the alternate crash also checked unchanged survivor container
identities/start times. The earlier two crash reports predate that added identity
assertion. Raw phase latency/count reports and full history are preserved in
`results/archive-20261115T212459.136233/`; current `benchmark-exit.json` records an
actual exited container, exit 0 and no OOM, alongside all 16 fresh measurements.

## Gotchas and limits

An initial benchmark interleaved exact scans before only some variants. Those
latency conditions were unequal; its timings are provisional and excluded above.
A corrected rerun returned Compose exit 0 without its final report; its actual
container state was unavailable because `--rm` removed it. Root cause remains
unconfirmed. The benchmark now retains the container, checks its actual exit/OOM
state and requires a fresh complete numeric artifact. Saved reports are fsynced.
A fresh setup also required one idempotent retry after a seed-marker error;
that failed attempt is retained and is not counted as a passing load. No populated
fixture was reset to convert a failed correctness check into a pass.

Only one host is used. Volume deletion is permanent owned-volume loss, not EIO or
physical power failure. The live reader samples immutable baseline points; it does
not measure fresh-write visibility lag. Scaling, partitions, quorum loss, rolling
upgrades, backup restore, disk-full and sustained saturation remain untested.
Lab peer traffic is plaintext; no production authentication/TLS claim is made.
Go uses standard libraries for REST, vectors, hashing, journaling and the oracle;
Python controls faults. This example contains its own application and Dockerfile.

## Sources

- [Qdrant 1.18 release, introducing TurboQuant](https://qdrant.tech/blog/qdrant-1.18.x/).
- [Pinned quantizer configuration](https://github.com/qdrant/qdrant/blob/v1.18.3/lib/segment/src/types.rs): this version uses `turbo.bits: bits4` and `always_ram`, rather than newer API examples.
- [Pinned local-shard APIs](https://github.com/qdrant/qdrant/blob/v1.18.3/src/actix/api/local_shard_api.rs).
- [Pinned write ordering](https://github.com/qdrant/qdrant/blob/v1.18.3/lib/collection/src/shards/replica_set/update.rs).
- [Distributed deployment](https://qdrant.tech/documentation/guides/distributed_deployment/).

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
