# Spark standalone order joins

One master, two one-core workers and a separate PySpark
driver join a million orders to 10,000 customers and persist Parquet.

## Quick start

1. `make docker-up` builds and starts the owned standalone cluster.
2. `make up` prepares the owned output volume.
3. `make test` runs the join and a separate reopen/verification application.
4. `make down` removes only the order dataset.
5. `make docker-down` removes the owned containers, volume, network and image.

## Contract

Every order must have the expected customer, amount and region, with exactly
1,000,000 distinct IDs from 0 to 999,999 and a total of 599,500,000 cents.
Each application verifies full row expressions plus independent expected totals.
The second process reopens Parquet without relying on the first driver's cache.
A separate Arrow Parquet reader verifies every body and extracts native footer
codec/size metadata.

The PySpark job uses the native supported API. JVM processes implement the
upstream Spark engine. The named volume is shared by all roles and resides
on one physical host. The separate [operator/HDFS example](../kubernetes-operator/)
verifies three storage copies and empty-disk replacement. Metadata HA and YARN
remain pending.

## Results

The initial million-order join, fresh Spark application readback and independent
Arrow 26.0.1 readback passed on 2026-11-26. Output has 1,000,000 distinct orders,
599,500,000 cents and zero missing, duplicate or wrong rows.

| Observation | Value |
|---|---|
| Registered workers / cores each | 2 / 1 |
| Output Parquet files / codec | 2 / Snappy |
| Parquet file bytes / allocated output | 5,895,188 / 5,951,488 bytes |
| Footer compressed / uncompressed column bytes | 5,886,252 / 11,689,335 bytes |
| Column compression ratio | 1.99× |
| Worker 1 / worker 2 sampled peak memory | 500.6 MiB / 558.4 MiB |
| Write-driver sampled peak memory | 497.7 MiB |

Native ARM64 Docker had 11 CPUs and 24.4 GiB available. Every Spark role had
1 CPU and 2 GiB limits. Each worker advertised 1 GiB of executor memory.
The Arrow verifier had 1 CPU and 512 MiB limits. Samples include rounded
lifetime block-I/O counters. IOPS/device latency, complete startup cost and
short-lived verifier usage remain incomplete. Builds/downloads overlapped this
functional run. No performance ranking or failure-recovery qualification follows.
[Compact report](reports/INITIAL_BATCH.json)

The first readiness probe used localhost although the master bound its named
interface. That probe failed before any application ran. The corrected probe
uses the master hostname and `make up` verifies both workers in the native
registry. Its original failed health observations remain in ignored results.

## Future work

- [ ] Complete phase resources including IOPS, short-lived actors and operation timing.
- [x] Kill a worker proven to host an active task; verify native retry events and complete output.
- [ ] Target a completed shuffle-output owner and verify lost-map recomputation separately.
- [ ] Test driver loss and idempotent publication/retry.
- [x] Add the separate private kind/operator shape.
- [ ] Add local-mode execution.
- [x] Verify empty DataNode-disk replacement in the separate operator/HDFS example.
- [ ] Add HDFS metadata failover and separate YARN scheduling.

[Spark architecture](https://spark.apache.org/docs/latest/cluster-overview.html)
· [Current batch](../../docs/example-plans/flink-doris-spark-storage.md)

[Declared baseline contract and client settings](config/contract.json).

## Worker recovery

On a fresh lifecycle, use `make worker-recovery` instead of `make test`. The
controller waits for the driver's native REST API to show an executor with an
active task, maps it to an owned worker container and SIGKILLs that worker.
It requires exit 137, restarts the worker and requires native executor-removal
and failed-task events. The Make target then runs both independent readbacks.
Native event histories remain in unique ignored run directories. This is task
recomputation over retained shared storage; driver loss and independent storage
recovery remain separate tests.

The 2026-11-26 recovery run passed: one native executor removal and one failed
task attempt, followed by application exit 0 and both exact readbacks. The
million-order total remained 599,500,000 cents, with zero wrong/missing/duplicate
rows. The 16-partition output used 6,554,890 file bytes / 6,688,768 allocated bytes
and Snappy column compression of 1.98×. Sampled worker CPU averaged 42.1% / 35.5%
of one host core, with sampled cgroup memory peaks 482.3 / 573.6 MiB. These are
recovery diagnostics, not an uncontended throughput comparison.
[Native recovery and resource report](reports/WORKER_RECOVERY.json).

The native driver REST endpoint may return HTTP 200 with the text
`Spark is starting up. Please wait a while until it's ready.` rather than JSON.
Two controller attempts stopped during that startup response before any output
was published. The readiness loop now recognizes that exact response and logs
all probe bodies; an unexpected non-JSON response still fails.

The shared workload and Arrow oracle now include endpoint restrictions, typed
schema/hash checks and pre-exit counters. Those additions ran in the operator/HDFS
lab; a fresh Compose run of the final source remains pending. Historical Compose
reports describe their recorded trials, not a new execution of this final source.
