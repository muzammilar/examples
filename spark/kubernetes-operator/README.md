# Spark operator order joins on HDFS

Initial functional/recovery qualification completed on 2026-11-27. Spark 4.3.0 applications are managed by
the official Apache Spark Kubernetes Operator 1.1.0 (chart 1.9.0). Two executors
join one million orders to 10,000 customers. Three HDFS DataNodes store three
copies; a single NameNode is an explicit metadata failure point.

## Quick start

1. `make kind-up` creates the owned four-node kind cluster and private kubeconfig.
2. `make up` builds/loads images, installs the operator and prepares owned HDFS storage.
3. `make test` runs the writer, live reader, fresh Spark/Arrow reads and storage audit.
4. `make down` removes all example applications, operator and namespace/storage.
5. `make kind-down` deletes the kind cluster, private kubeconfig and built images.

Docker, kind, Helm, Python 3, Git and shasum are required. The example installs a
checksum-pinned local kubectl 1.37.3; no host tooling or default context changes.
Every cluster command specifies `kind-spark-kubernetes-operator`. Source links
are materialized into ignored `.build/` as hard links/copies before Docker builds.
No sibling Makefile, generated output, cluster or prior state is required.

## Layout and contract

- `orders-workload/`: meaningful link to repository-owned PySpark source plus its Dockerfile.
- `parquet-verifier/`: exact typed Arrow oracle and a reader for visible closed parts.
- `docker/Dockerfile.hadoop`: checksum-pinned Hadoop 3.5.0 runtime and verifier stages.
- `config/contract.json`: declared workload, publication, retry and durability boundaries.
- `config/hadoop/`: three-copy target, minimum two replicas for block completion.
- `deploy/`: private kind, HDFS roles, operator values and application/reader manifests.
- `scripts/`: local tool preparation, capacity checks, lifecycle and storage controllers.
- `reports/`: curated measured outcomes; raw/generated evidence remains under ignored `results/`.

Spark writes 1,000,000 unique IDs, 10,000 customer IDs and 599,500,000 cents.
The live reader checks each visible closed part's bodies. Before `_SUCCESS`, it
makes no whole-directory snapshot or global uniqueness claim. After publication,
fresh Spark and Arrow processes must verify every body and the complete ID set.
Native application/driver success is required as well; resource cleanup alone
never qualifies an application. Whole-application retries are disabled, and failed
output is never automatically reset or reseeded.

HDFS has target RF3 with a configured minimum of two replicas for block completion.
This is distinct from three independent hosts: all kind workers share one VM.
The lab accelerates dead-DataNode detection using 1-second heartbeats and a
1,000-ms NameNode recheck interval. These are bounded-test settings, not production
recommendations; they can cause false failure detection under scheduler pressure.
Simple authentication is lab-only. NameNode HA/fencing, independent restore and
physical host/power-loss durability remain unqualified. Object storage/AWS is not
used; workload paths fail closed outside the named local/HDFS endpoints.

## Recovery cases

On a fresh lifecycle, use `make test-production` instead of normal `make test`.
The executed serial matrix was active-task executor loss followed by retained-data
verification and one empty DataNode-disk replacement. Source hashes, operation
errors, native task events, block placement/hashes and resource counters are retained.
The first trial ran the native event-proof checker separately; it is now wired
into the target, but that exact final orchestration awaits a fresh lifecycle repeat.
Live write/ACK histories, driver loss, metadata HA, scaling, upgrades, pressure and
a matched level-3 comparison remain follow-ups.


## Measured initial results

These are finite correctness/recovery diagnostics, not throughput rankings.
[Curated report](reports/INITIAL_RESULTS.json) records environment, effective
settings, source hashes, per-Pod CPU/memory/I/O samples and replica hashes.

| Check | Observed result |
|---|---|
| Active executor loss | One observed active executor removed; one matching native removal and one failed task; driver exited 0 and operator recorded success |
| Writer observation | 87.830 s including submission, fault, output/readback and controller observation; not an isolated failover RTO |
| Live/fresh readback | All 1,000,000 unique orders and 599,500,000 cents correct against a 10,000-customer dimension; live reader, fresh Spark and independent Arrow passed |
| Physical replicas | All 16 published Parquet files independently hashed on all three separately placed DataNodes |
| Empty disk replacement | New PVC UID, volume and DataNode UUID; unchanged NameNode identity; complete reads with two surviving copies and after RF3 repair; 110.724 s including verification |
| Parquet | 6,554,890 file bytes; Snappy column bytes 6,528,906 versus 12,925,420 decoded column bytes, ratio 1.980 |
| Native storage after tests | Each DataNode 8,343,585 logical / 8,491,008 allocated bytes; NameNode 1,049,268 / 1,077,248 bytes, including event logs and metadata |
| Writer resources | Native driver memory peak 600,604,672 bytes; pre-exit CPU 13.740 core-seconds; 1,191,936 disk-write bytes / 19 writes, cached reads reported zero |

CPU samples normalize one core to 100%; observer overhead, helper JVM startup and
charged cache are included where measured. Start/exit gaps make sampled CPU/I/O
deltas lower bounds. Network byte accounting and device service latency were not
measured; these gaps prevent a holistic performance comparison. No continuously
acknowledged external writer ran during disk replacement. The live reader observes
closed parts, not pre-commit transactional snapshots.

Two setup problems remain documented as failures: image loading consumed much
more space than Docker's image-size estimate predicted; the initial DataNode mount
failed `chmod` with EPERM. The preflight now reserves conservative unpack/snapshot
space (40 GiB before creating kind, plus a size-based import check), and DataNodes
mount the parent of their native directory to allow non-root ownership. The trial
recovered on retained NameNode/PVC state. Authorized unused build-cache cleanup
and removal of this example's host-built images after worker import restored disk
headroom; unrelated images, volumes and containers were preserved. A conservative
preflight is not a reservation: monitor actual free space while importing.

HDFS full-JMX health probes also produced connection-reset log noise while native
repair was progressing. This is not evidence of lost data; a smaller semantic
probe and sustained live-read availability checks remain follow-ups.

[Official operator](https://apache.github.io/spark-kubernetes-operator/)
· [Python application source example](https://github.com/apache/spark-kubernetes-operator/blob/1.1.0/examples/pi-python.yaml)
· [Native resource retention](https://github.com/apache/spark-kubernetes-operator/blob/1.1.0/spark-operator-api/src/main/java/org/apache/spark/k8s/operator/spec/ResourceRetainPolicy.java)
