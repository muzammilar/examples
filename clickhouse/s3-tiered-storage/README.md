# ClickHouse: hot/cold MergeTree on Garage S3

Three replicated ClickHouse servers keep recent events on local disk and move
old monthly parts to a replicated Garage S3 store using native TTL movement.
A filesystem cache demonstrates how the same cold query changes after warming.
This is native MergeTree storage; the existing [Iceberg example](../iceberg/)
covers open-table interoperability and archive handoffs separately.

## Quick start

1. `make docker-up` — start nine Garage nodes, S3 routing and three Keepers.
2. `make up` — build the client, start three ClickHouse replicas and seed events.
3. `make test` — move old parts to S3 and reconcile all three replicas.
4. `make down` — synchronously drop the example table; keep infrastructure running.
5. `make docker-down` — remove this project's services, disks and built clients.

Requires Docker Compose with `include` support (tested 5.1.4), Python 3, at least 20 GiB Docker RAM and 15 GiB free
Docker disk. Credentials are local fixtures and services use the private Docker
network. Real production deployments need independent hosts/disks, redundant S3
routing, TLS, managed credentials, monitoring, backups and capacity headroom.

## Architecture

| Component | Version / geometry | Per-service cap |
|---|---|---|
| ClickHouse | 26.10.2.103; one shard, three ReplicatedMergeTree copies | 2 CPUs / 2 GiB |
| Keeper | 26.10.2.103; three voting members | 0.5 CPU / 384 MiB |
| Garage | 2.5.0; nine nodes, three zones, three copies, quorum 2 | 1 CPU / 768 MiB |
| S3 routing | HAProxy 3.2.8-alpine | 0.5 CPU / 128 MiB |
| Python Docker client | Standard-library HTTP/JSON; native SQL | 2 CPUs / 1 GiB |

Aggregate store caps: 17 CPUs / 14 GiB (CPU oversubscribed on the measured
11-CPU Docker VM). Each ClickHouse replica has its **own S3 prefix**, local S3
metadata and a 512 MiB filesystem cache. Zero-copy replication is disabled;
replicas exchange parts through authenticated native interserver HTTP. Replication
at the ClickHouse and Garage layers has separate cost and failure semantics;
content-addressed backend deduplication means this is not a claim of nine
independent physical payload copies.

`ROWS=2000000`, 10,000 tenants, 20 batches of 100,000 rows; half the data is 62
days old and half is current. Tenant/time queries use `ORDER BY
(tenant_id,event_time,event_id)`; retention uses monthly partitions. Native
`UInt16`/`UInt64`, `LowCardinality` event types and binary SHA-256 payloads keep
types explicit. TTL moves old parts to the `cold` volume; it does **not** delete
rows. `move_factor=0` prevents capacity-pressure moves from obscuring this test.

These are workload-derived design choices informed by ClickHouse rules
`schema-pk-prioritize-filters`, `schema-partition-lifecycle`, `insert-batch-size`
and native type-width guidance. The TTL/cache/replication mechanisms are official
features; the following performance and recovery values are local observations.

## Correctness and measured performance

Measured 2026-11-15, Apple M4 Pro, Docker Linux aarch64, 11 CPUs / 24.42 GiB RAM.
Ceph guests were booting/provisioning on the same host: these are **shared-host
observations**, not isolated capacity numbers. All services had the caps above.

The initial 2,000,000-row server-generated `INSERT SELECT` took **1.820 s /
1,098,678 rows/s**, including quorum=2 inserts and final replication sync;
correctness checks are outside this timer. This is not external client/network
ingestion throughput. Each replica then had 1,000,000 hot rows and 1,000,000
S3-backed rows after **6.49 s** of TTL convergence/checking. Counts, unique IDs, sums, every deterministic payload/derived
field and timestamps matched on all three replicas. Native `CHECK TABLE` verified
part checksums, rejecting incomplete checks as well as failures.

`make benchmark`, `QUERY_RUNS=20`: one untimed warmup and 20 serial queries per
phase, tenant 42, 100 baseline rows, event-type count/sum aggregation. Both counts
and amounts match an independently computed expected result. Query cache and
uncompressed cache are disabled; bounded query settings and `EXPLAIN indexes=1`
are saved with results.

| Query path | QPS | p50 ms | p99 ms |
|---|---:|---:|---:|
| Hot local parts | 280.68 | 2.85 | 14.29 |
| S3, filesystem cache disabled | 3.93 | 250.75 | 336.78 |
| S3, warmed filesystem cache | 254.95 | 3.83 | 4.71 |

Cache-disabled is not a physically cold S3/disk benchmark; backend and OS caches
may be warm. Twenty samples make p99 the slowest sample, not a stable tail estimate.
The narrow tenant query illustrates remote-read latency and cache benefit for
this schema rather than whole-table scan capacity.

## Separate recovery tests

Run after `make test`; each target requires a fresh case directory, so use a clean
lifecycle before repeating it:

| Target | Failure | Outage | Recovery after restart command | Batches ACKed | Rows ACKed during outage |
|---|---|---:|---:|---:|---:|
| `make failover` | SIGKILL ch0; retain disks | 30.50 s | 6.29 s | 37 | 240,000 |
| `make disk-failure` | Destroy ch0's owned local metadata/cache/data volume | 30.67 s | 10.70 s | 41 | 240,000 |
| `make backend-failure` | SIGKILL Garage node0 | 30.53 s | 6.41 s | 15 | 20,000 |

The producer keeps inserting unique 10,000-row batches on ch1 with quorum 2
while ch2 reads cold S3 parts with the filesystem cache disabled. An external
fsynced attempt/ACK journal survives the injected failure. The client never
blindly retries an ambiguous insert. In these runs all **93 attempts were ACKed**,
with zero write/read request errors. Final reconciliation required every ACKed
batch, exact deterministic values, no duplicate IDs, agreement across replicas
and valid part checksums: **2,930,000 rows per replica**, including 930,000 fault
rows; all error counters zero. The other 15 surviving containers retained their
identities in each experiment.

## Operational gotchas and limits

- Remote S3 bytes alone do not restore a ClickHouse S3 disk: the mapping/metadata
  lives locally. Disk recovery drops the inactive replica's Keeper registration
  and creates an empty replica from a healthy peer's native DDL. The surviving
  replicas are essential to this tested recovery path. Protect local metadata,
  Keeper and backups; this does not qualify total-cluster loss recovery.
- Old remote objects from a lost metadata disk can be orphaned. The harness
  never blindly deletes a live prefix. Full `docker-down` removes the example's
  owned backend; production orphan reclamation needs deliberate native tooling.
- Garage replication and ClickHouse replication are two distinct layers. Extra
  copies do not make shared physical power/storage independent. Only a single
  backend member fails here; the standalone [Garage example](../../garage/)
  independently qualifies empty storage-disk replacement and physical block repair.
- TTL movement is asynchronous. The test waits until every replica reports at
  least the baseline million cold rows, then verifies values and native checksums.
  Moving to S3 is not an Iceberg export, backup or table deletion.
- Keeper sessions must expire before removing a dead replica registration;
  deleting an active replica is rejected. Reusing a replica name while its old
  session is alive is not safe recovery.
- One shard here isolates storage-tier behavior. The existing [multi-system
  example](../../iceberg/multi-system/) has a three-shard/three-copy serving setup.
- The local load balancer, single physical host and fixed fixture credentials
  are lab boundaries. Host loss, correlated disk failure, network partitions,
  concurrent multi-node loss, offsite restore and production sizing remain untested.

## Files and sources

```text
clickhouse/s3-tiered-storage/
  Makefile
  docker-compose.yml           # includes scoped Garage services
  app/{Dockerfile,workload.py,fault_workload.py}
  scripts/{config.py,fault.py}
  config/generated/            # ignored native Keeper/ClickHouse XML
  results/                     # ignored journals, EXPLAIN and measured results
../../garage/s3-cluster/        # native backend configuration/bootstrap
```

Official [S3 disk/cache configuration](https://clickhouse.com/docs/operations/storing-data),
[TTL movement](https://clickhouse.com/docs/guides/developer/ttl),
[replication](https://clickhouse.com/docs/engines/table-engines/mergetree-family/replication),
[CHECK TABLE](https://clickhouse.com/docs/sql-reference/statements/check-table).
Pinned native source inspected:
[`S3ObjectStorage.cpp`](https://github.com/ClickHouse/ClickHouse/blob/v26.10.2.103-stable/src/Disks/ObjectStorages/S3/S3ObjectStorage.cpp).

## Future work / TODO

Deferred checks for supported configurations; these are **not executed coverage**.
Keep recorded passes, failures and limitations above. Performance work requires
a separately selected depth; this list does not restart paused experiments.

- [ ] Audit independent live readers, durable historical witnesses and native repair proof for every configured object-storage backend.
- [ ] Exercise local mapping/metadata loss separately from remote-object, Keeper and gateway outages.
- [ ] Verify independent backup/restore and orphan-object retention/cleanup; surviving S3 objects alone do not reconstruct local disk metadata.
