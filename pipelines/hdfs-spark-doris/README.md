# HDFS → Spark → Doris

An isolated batch-analytics pipeline: HDFS RF3 holds Parquet inputs, two Spark
workers join orders with customers and build regional aggregates, and three Doris
FEs/BEs serve the published output at RF3. This is a cross-system workload;
individual Spark, Hadoop and Doris deployment examples remain separate.

## Quick start

Requires Docker Compose, Python 3.11+, approximately 11 CPUs / 24 GiB for Docker
and sufficient local disk for the pinned images. No host language toolchain,
cloud account, host mounts or published ports are required.

1. `make docker-up` — build images and prepare this example's private infrastructure.
2. `make up` — seed input once, publish verified output and load it into Doris.
3. `make test` — independently verify files, all three Doris copies and stable receipts.
4. `make down` — verify acknowledged output and stop the example.
5. `make docker-down` — remove only this example's containers, network and volumes.

Set `ORDER_COUNT=1000000 CUSTOMER_COUNT=10000` on every invocation, or use those
Make defaults. A preserved fixture rejects changed counts. Repeating `docker-up` resumes only the same owned fixture/configuration. `make up` against an
existing fixture verifies and reuses its input/output; it never resets data.

## Data path and files

| Component | Implementation |
|---|---|
| Three-copy HDFS; one NameNode | `compose.yaml`, `deploy/hadoop/`, `docker/Dockerfile.hadoop` |
| Native Spark input, join and regional aggregation | `orders-workload/orders.py`, `docker/Dockerfile.spark` |
| Publication manifest validation and fresh Arrow oracle | `scripts/publication.py`, `scripts/verify_parquet.py` |
| Independent full-row SQL oracle and stable load labels | `serving-oracle/` (Go and maintained MySQL driver) |
| Lifecycle, role faults and resource observations | `scripts/`, `Makefile` |
| Curated executed evidence | `reports/`; raw evidence remains ignored in `results/` |

Orders are one-based IDs, with deterministic customer, amount, region, revision
and event-time values matching the CDC pipeline's initial logical recipe.
Customer IDs span 10,000 entries; orders span one million. Input is partitioned by UTC event day; input and output use
Parquet/ZSTD and verified publication manifests. Spark performs a shuffle join, partitions files by customer modulo
three and separately aggregates counts/amounts by region. Three file partitions
are a logical layout; Doris uses its own three hash buckets and RF3 tablets.
These are different routing algorithms.

Spark writes an unpublished staging directory with FileOutputCommitter algorithm
1. After exact row verification it records the file inventory and HDFS checksums,
then renames the whole directory into the published namespace. A fresh Arrow
process downloads the published files and independently checks every field,
customer partition, ID and regional total. Doris requires two successful load replicas out of three (`min_load_replica_num=2`);
final readback requires all three current copies. Doris loads only this verified path
using native HDFS TVFs with strict inserts and stable labels. Retrying first
reconciles FINISHED native load receipts; it does not blindly execute mutations.
Labels have finite server retention: this is a bounded retry test, not permanent deduplication.
The order and aggregate loads are separate atomic transactions, **not one
cross-table transaction**. Verification must gate application publication of a
batch. A missing HDFS path can produce an empty TVF, so SQL success is insufficient.

Pinned/tested stack: **Spark 4.3.0**, **Hadoop 3.5.0**, native **Doris
4.1.5 (`917b20be838`)**, Temurin **17.0.21**, Python **3.12.3**, Arrow **26.0.1**,
Go **1.27.4** and MySQL driver **1.11.0**. Registry base digests are in the local
Dockerfiles; executed local image IDs, effective limits and the SQL client
contract are preserved in the curated report. Doris's MySQL wire version is not
its engine release identifier.

## Focused recovery commands

- `make executor-failure` — kill a worker proven to host an active task; preserve
  native failed-task evidence and verify the recovered publication and Doris data.
- `make datanode-failure` — kill one DataNode during an active Spark job, restore it
  and verify complete publication and restored HDFS replication.
- `make test-production` — run those cases serially and verify the baseline again.
- `make benchmark` — three short, verified warm-query trials for point reads and
  the 16-row materialized regional rollup; these do not measure a raw million-row scan.
- `make report` — curate fixture-scoped results after the full lifecycle and fault cases.
- `make observe-repair` — after a failed repair deadline, observe stock automatic
  reconstruction and verify retained physical copies; it preserves the failed case.
- `make resources` — native role counters, topology and owned storage snapshots.

Each failure uses a distinct immutable output batch. Repeated execution of the
same destructive target is intentionally rejected unless a new owned fixture is
created: an existing batch would skip computation and could not prove a new fault.
The one-million-row workload is not a capacity or saturation benchmark.

## Results and limitations

Executed on ARM64 Docker Desktop (11 CPUs, 24.42 GiB RAM), with all service and
profile-tool caps totalling **10 CPUs / 20.75 GiB**, plus 2 GiB reserved headroom.
The original preflight/header accidentally omitted profile-only tools; the
[report](reports/QUALIFICATION.json) preserves that header and a verified budget
correction. Actual resource limits did not change. The approved depth is level 3.

| Case | Measured outcome |
|---|---|
| Healthy batch | 1,000,000 exact joined orders, 10,000 customers, 16 exact regional totals; three customer partitions and three fixed Doris replica reads passed |
| Input / joined publication | Spark-reported 10.634 / 13.644 s, including validation; Spark startup and external verification are additional |
| Doris bulk import | 4,754.249 ms for the million-order INSERT; 404.438 ms for regional totals |
| Worker SIGKILL | One native failed task; complete million-row publication in 15.351 s of Spark workload time, unchanged bodies/receipts; historical reader passed during the outage |
| DataNode SIGKILL | Complete million-row publication in 26.229 s, no failed Spark task attempts; native HDFS pipeline errors/retries occurred; historical reader and all three Doris reads passed |
| **RF3 repair deadline — failed** | 14 published blocks still had two copies after the 180-second repair gate; zero missing/corrupt blocks. `fsck` still said HEALTHY |
| Automatic eventual repair | All 14 requests timed out at **385.539–385.540 s after injection**; full RF3 verified by **430.780 s**. No reset, config change, NameNode restart or reduced replication |
| Recovered physical copies | 75 published HDFS blocks / 225 matching physical copies, 24,187,479 bytes read directly; 18 current Doris tablet replicas |
| Retry receipts | Two stable native INSERT labels per batch; retries reused the receipts, with no additional load transaction |

There are three separately named million-row output batches over the same input,
not three million distinct source orders. Event times are synthetic 2023 dates;
the physical data was only minutes old, so this does not establish long-term
storage or cold-read behavior. All have amount sum **599,500,000 cents**.
The 430.780-second value is the first complete verification observation, **not an
exact timestamp of repair completion**. The original failing phase remains failed;
`make observe-repair` records eventual recovery separately. `make test-production`
therefore returned nonzero in this run and stopped before its final `verify` step.
Final `down` reconciliation passed for every acknowledged batch, and the complete
five-command lifecycle removed all owned containers, network and volumes.

The stock pending-reconstruction timeout is **300 seconds**, verified from the
running NameNode's configuration. Its native monitor checks every 300 seconds,
which can produce a 5–10 minute retry tail. Pending requests can coexist with zero
`LowRedundancyBlocks`; monitor pending work, actual replica counts and elapsed
repair time together. The [failure/recovery notes](docs/RECOVERY.md) retain the
mechanism, commands and limits. This is a failed lab deadline, not observed data
loss or proof that Hadoop violates a promised 180-second recovery guarantee.

Three short warm-query trials (30 measured calls + 5 warmups each, 100 ms think
time, alternating order):

| Query | Mean ms: trial 1 / 2 / 3 | Largest observed call |
|---|---|---|
| Point lookup, eight older persisted IDs | 12.218 / 10.214 / 11.021 | 50.916 ms |
| Materialized 16-row regional rollup | 14.677 / 16.182 / 12.016 | 71.418 ms |

These are diagnostics, **not a qualified competitive winner**, a raw-million-row
scan, a capacity test or physical cold-cache evidence. With 30 samples, reported
p99 is the observed maximum; p99.9 is unavailable.

| Resource scope | Recorded CPU core-s | CPU mean / peak % | Sampled memory mean / peak GB | Retained-role disk read / write bytes | Read / write I/O operations |
|---|---:|---:|---:|---:|---:|
| Seed/publication/readback (`up`) | 190.382 | 144.72 / 283.35 | ≈7.22 / 7.86 | 1,216,512 / 83,275,776 | 131 / 2,487 |
| Worker case | ≥161.723 | 126.47 / 305.82 | ≈7.74 / 8.29 | ≥122,880 / ≥69,238,784 | ≥8 / ≥2,109 |
| DataNode case, including failed deadline | ≥304.379 | 96.32 / 291.62 | ≈7.91 / 8.94 | ≥2,588,672 / ≥77,078,528 | ≥88 / ≥2,968 |
| Warm-query diagnostics, including pre-verification | 43.724 | 94.05 / 132.55 | ≈7.96 / 8.05 | 0 / 3,874,816 | 0 / 354 |

CPU utilization uses **one core = 100%**. Query-window writes include background
logs/maintenance; zero measured reads reflect these warm trials, not a cold-read
claim. The settled owned-volume inventory summed **374,464,512 allocated-file
bytes / 134,257,858 apparent-file bytes** across data and metadata copies.
Allocated-file blocks are not unique physical-device allocation accounting.

CPU includes recorded actor lifetimes and retained-role deltas. Disk figures above
are retained-role deltas; actor lifetime counters remain separate in the report.
Restarted worker/DataNode lifetime gaps make fault totals incomplete. Docker memory
samples use rounded display values. Native high-water counters and raw network
samples are retained. The initial repair gate launched Java CLI probes inside the
NameNode container; their cost is included, not isolated engine CPU. Repair itself
completed in a collector gap; **isolated repair CPU/I/O is unavailable**. Later
physical verification measurements must not be presented as the cost of repair.

The healthy published Parquet files occupy **2,682,754 bytes**. Column chunks total
**16,268,725 uncompressed / 2,633,094 compressed bytes**, measured **6.179× ZSTD**.
This is encoded Parquet column compression, not whole-stack space amplification.
Arrow body verification used 0.940 CPU-s; that is not isolated compression CPU.
Doris native uncompressed bytes/compression ratio and transient shuffle peak
storage remain measurement gaps. Owned-volume allocation and per-phase counters
are in the report. [Pre-load setup/runtime failures](docs/SETUP-FINDINGS.md) also
remain documented.

This lab has a single NameNode, Spark master and driver. RF3 does not make its
metadata highly available; see the separate Hadoop metadata-HA example. All roles
share one Docker VM and physical disk. It cannot establish host/AZ/power-loss
survival. No Kerberos or production authentication is configured on the private
lab network. Cross-table publication, driver loss, commit-boundary response loss,
whole-disk loss, independent restore and long-term retention remain future work.
A retained mount/process recovery is not an independent backup. Physical hash-bucket
placement and replica selection must be checked from native tablet evidence,
not inferred from the modulo-three file paths.

## Sources

- [Spark 4.3 commit protocol source](https://github.com/apache/spark/blob/v4.3.0/core/src/main/scala/org/apache/spark/internal/io/HadoopMapReduceCommitProtocol.scala)
- [Hadoop 3.5 FileOutputCommitter](https://github.com/apache/hadoop/blob/rel/release-3.5.0/hadoop-mapreduce-project/hadoop-mapreduce-client/hadoop-mapreduce-client-core/src/main/java/org/apache/hadoop/mapreduce/lib/output/FileOutputCommitter.java)
- [Doris HDFS TVF and empty-path behavior](https://doris.apache.org/docs/4.x/sql-manual/sql-functions/table-valued-functions/hdfs/)
- [Doris INSERT labels and atomicity](https://doris.apache.org/docs/4.x/sql-manual/sql-statements/data-modification/DML/INSERT/)
