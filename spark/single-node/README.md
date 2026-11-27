# Spark local order joins

One Spark JVM using `local[1]` joins one million orders to 10,000 customers and
writes Parquet. A fresh Spark application and an independent typed Arrow reader
verify every order. The initial lifecycle and read-process crash case passed on
2026-11-27; this is not a distributed deployment or capacity benchmark.

## Quick start

1. `make docker-up` builds images and initializes the example-owned volume.
2. `make up` verifies writable storage without resetting existing output.
3. `make test` writes the dataset and runs fresh Spark/Arrow readbacks.
4. `make down` removes the published dataset after preserving needed evidence.
5. `make docker-down` removes owned services, volume, network and built images.

Docker Compose, Make, Python 3 and Git are required; no host Spark/Java/PyArrow
installation is needed. These are one-shot Compose services: `docker-up` prepares
infrastructure and exits. The internal network has no published ports or external
storage credentials. Workload paths are restricted to the named local endpoints.

## Files and contract

- `orders-workload/`: Dockerfile and meaningful source link to the tracked PySpark workload.
- `parquet-verifier/`: tracked-source link to the independent Arrow verifier.
- `docker/`: pinned verifier Dockerfile; Spark 4.3.0 and Arrow 26.0.1 match cluster examples.
- `config/contract.json`: operation, publication, retry and durability boundaries.
- `scripts/`: local source preparation, immutable run logs, resource observation and crash controller.
- `reports/`: curated outcomes; generated/raw evidence remains ignored under `results/`.

`make prepare` materializes the two repository-owned source links as hard links
or copies under ignored `.build/`. Each example owns its build context and data;
no sibling Makefile or generated artifacts are required. Never edit materialized
hard links. Application Python uses the supported PySpark API; it is a system API
exception to the normal Rust/C++/Go application preference. Management uses Python
standard libraries and the Docker CLI/API; no custom Java helpers are introduced.

`local[1]` runs scheduling and task execution in the same JVM with one task thread;
it is not a standalone master/worker cluster. Spark's native local scheduler
constructs an in-process executor. PySpark adds a Python driver process. Every
order must have ID 0..999999 exactly once, customer ID `order_id % 10000`, amount
`100 + order_id % 1000`, and region `customer_id % 10`, totaling 599,500,000 cents.
Spark tasks use native retries; whole-application restart is disabled. Existing
output rejects another write through `errorifexists`; ambiguous writes require
inspection. `_SUCCESS` is a completed file-set marker, not a database transaction.

## Operational checks and measurements

`make reader-crash` requires an already verified dataset. It waits for an active
native task in a read-only local application, SIGKILLs only its owned container,
requires exit 137, then runs fresh Spark/Arrow checks. This tests loss of a reader,
not interrupted writer publication or durable task recovery. Keep raw fault
probes and failed results. `make test-production` serially runs the initial
workload and reader-crash case on a fresh lifecycle. It is initial coverage only.

For resource capture use `python3 scripts/observe.py test` and then separately
`python3 scripts/observe.py reader-crash`. Measurements are diagnostics at the
approved batch Level 3, not an isolated cross-engine benchmark. Report native
compression and logical/allocated bytes separately; CPU, charged memory and disk
I/O include all actors where sampled. Missing network/device-latency measurements
must remain explicit. Cold reads are unqualified: a new process does not evict
the shared VM cache. Never clear host caches globally.

## Measured first pass

[Curated results](reports/INITIAL_RESULTS.json) preserve source identities,
phase durations, complete-file hashes, native pre-exit counters and sampled actors.
The same one-million-row body and 599,500,000-cent total passed before and after
the read-only process crash; all 16 original Parquet file hashes were unchanged.
The native REST probe observed one active task in the in-process `driver` executor;
Docker then recorded SIGKILL exit 137. Fresh Spark and Arrow checks passed.

| Observation | Measured value |
|---|---|
| Write + in-application correctness, including startup | 11.426 s |
| Separate Spark full-body read application, including startup | 9.403 s |
| Independent Arrow decode/body/hash check | 199.642 ms baseline; 199.107 ms after crash |
| Parquet file / allocated output bytes | 6,554,890 / 6,688,768 |
| Snappy column compressed / uncompressed bytes | 6,528,906 / 12,925,420; 1.980 ratio |
| Writer native pre-exit CPU / charged memory peak | 10.822 core-seconds / 757,497,856 bytes |
| Writer observed cgroup read / write bytes | 7,446,528 / 147,456; 199 read / 2 write operations |
| Sampled writer CPU mean / charged memory maximum | 98.62% of one core / 669,048,832 bytes |

Docker provided 11 CPUs and 24.4 GiB memory on ARM64 Linux; no other lab ran.
Spark is capped at 1 CPU/2 GiB, Arrow at 1 CPU/512 MiB. CPU samples and native
pre-exit totals cover different windows. Cgroup writeback is buffered: 147,456
observed write bytes is not the complete physical cost of the 6.55 MB output.
Empty I/O counter lists are unavailable, not zero. Samples can miss short-lived
actors; Arrow also emits native pre-exit CPU/peak-memory counters. No network-byte,
device service-time or verified cold-cache comparison was collected. The timings
are a single functional diagnostic trial and cannot establish a performance winner.
The two components of `test-production` ran separately with observers; the aggregate
target itself has not been executed on a new empty lifecycle.

## Remaining coverage

Interrupted writer publication, corrupt files, bounded disk-full, independent
restore and physical power loss remain unrun. This volume has one failure domain
and no replication. Separate [Compose](../docker-compose-cluster/) and
[operator/HDFS](../kubernetes-operator/) examples cover distributed execution.
A matched Level-3 comparison with other engines has not run.

[Submission and local modes](https://spark.apache.org/docs/4.3.0/submitting-applications.html)
· [Versioned local scheduler source](https://github.com/apache/spark/blob/v4.3.0/core/src/main/scala/org/apache/spark/scheduler/local/LocalSchedulerBackend.scala)
