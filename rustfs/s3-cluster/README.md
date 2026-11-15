# RustFS: S3 cluster and disk recovery

S3 with native distributed erasure coding, protected shard integrity and disk healing.

## Quick start

1. `make docker-up` — start the owned storage infrastructure.
2. `make up` — build the SDK client, configure the workload and seed data.
3. `make test` — run negative controls, S3 semantics and full-body reconciliation.
4. `make down` — delete the workload bucket and objects; keep infrastructure running.
5. `make docker-down` — remove owned containers, volumes, network and client image.

Requires Docker Compose and Python 3. Use a Docker VM with at least 20 GiB RAM
and 12 GiB free disk for this example alone. Fixture credentials are scoped to a
local private Docker network; production TLS, secret management and redundant
gateways are deployment work. No host block devices or privileged containers.

## Workload and test contract

Defaults `OBJECTS=10000`, `LARGE_OBJECTS=128`: 10,133 final objects /
210,833,408 bytes after the API suite. Objects are incompressible, unique and
externally checksummed. The suite checks full PUT/GET/HEAD, pagination, 100-byte
ranges, Unicode keys, copy, four overwrite versions, delete, 32 MiB multipart
completion, multipart abort and invalid Content-MD5 rejection. Eleven shared negative
controls test corruption, truncation, stale data, unavailable responses, missing
ACKs, pagination, partial cleanup errors and preserving the historical oracle.

`make failover` and `make disk-failure` are separate experiments. A continuous
32 KiB producer runs through the outage and repair. Its fsynced attempt/ACK
journal lives outside the store. There is one SDK attempt per request; ambiguous
outcomes are reported and checked against the attempted body, never blindly
replayed. Every acknowledged object must survive with exact SHA-256, length and
application version. All earlier fault journals are rechecked on subsequent runs.

## Measured recovery

2026-11-15, Apple M4 Pro; Docker Linux aarch64, 11 CPUs / 24.42 GiB RAM.
The fault runs shared this host with Ceph guest provisioning. Counts are cumulative
and include preserved diagnostic attempts; they are not a fresh fixed-size run.

| Injection | Attempts / ACKs | Ambiguous PUTs | GET errors | ACKs during outage | First ACK after fault | Ready after restart | Final objects |
|---|---:|---:|---:|---:|---:|---:|---:|
| process: rustfs0 | 1,298 / 1,296 | 2 | 1 | 293 | 11.84 s | 6.46 s | 14,351 |
| disk: rustfs0 | 1,298 / 1,297 | 1 | 2 | 327 | 0.01 s | 6.38 s | 15,648 |

Every listed case passed: **zero lost acknowledged objects, corrupt bodies,
stale versions or unknown keys** after recovery. Outages lasted approximately
30–31 seconds. Ready time starts at restart; it excludes the outage and native
repair/full audit, so it is not total recovery time. An ambiguous response can
mean a committed PUT; it is not automatically data loss. Final post-benchmark
reconciliation verified **36,416 objects /
2,084,081,664 bytes** with all four error counters zero. Both
layered cleanup commands completed successfully, including bucket deletion.

## Topology and recovery gotchas

| Role | Count | CPU / RAM per service |
|---|---:|---|
| RustFS 1.0.5 | 4 | 2 / 1,536 MiB; one owned data volume per node |
| HAProxy 3.2.8-alpine | 1 | 0.5 / 128 MiB |
| Docker-built Boto3 1.44.3 client | 1 | 2 / 2 GiB |

Store caps total 8.5 CPUs / 6.125 GiB. The four-drive erasure set explicitly
uses **EC:2 (2 data + 2 parity)** with strict durability. Source-derived read
quorum is 2 and write quorum 3; one unavailable node leaves three writable
members. Two concurrent failures were not injected or qualified here.

- The native same-device safety check stays enabled. Four directories on the
  same local device were rejected by 1.0.5, so the example uses the supported
  four-node/one-drive layout rather than enabling the unsafe bypass. Separate
  container mounts still share this machine's physical storage: they do not
  establish independent-host or power-loss durability.
- Both `RUSTFS_SHARD_INTEGRITY_WRITE` and
  `RUSTFS_SHARD_INTEGRITY_FLEET_CONFIRMED` are true for this fresh, uniformly
  pinned fleet. Fleet confirmation is an operator assertion, not automatic
  mixed-version compatibility detection. Legacy MinIO adoption, rolling
  upgrades and rollback are excluded from this example's qualification.
- `make disk-failure` permanently deletes rustfs0's owned data volume. The
  completed ownership-init container must release its mount reference first;
  otherwise Docker correctly refuses volume removal. The replacement is empty
  and chowned to the native image UID 10001.
- `make repair` invokes the signed native administrative deep-heal API.
  Success requires canonical execution `completed`, coverage `complete`, no
  failed/skipped/unknown outcomes and no counter overflow. The legacy
  `objectsHealed` field alone is insufficient proof of repairs.
- Native deep heal checks protected data; an independent read-only audit
  requires all four `xl.meta` copies and matching SHA-256 for all **134 baseline
  finalized payload shards** (`part.N`) at their exact relative paths. Old
  multipart staging metadata is deliberately excluded. After disk replacement,
  15,648 objects had 62,592 physical metadata copies and zero missing or changed
  baseline payload shards. Full S3 reconciliation also covers new churn objects.
- Strict degraded LIST has upstream edge cases around stale/deleted keys.
  Complete listing is reconciled after heal; degraded LIST is not a backup
  inventory guarantee. An ACK, successful surviving GET and complete repair
  are three different milestones.
- RustFS reached GA in September 2026 and has a shorter operational record than
  Ceph. This is a tested fresh 1.0.5 configuration, not a claim of equivalent
  maturity, legacy repair support, offsite recovery or production certification.

## Benchmark

`make benchmark` measures fresh unique incompressible objects; request generation
is outside the timer, SDK signing/checksums/transfer and full GET hashing are
inside. GET follows PUT and is warm. Ceph guests were paused during each measured
run; no other storage SUT was active. Their retained RAM and the caps above still
form part of the environment. Different replication geometry/caps prevent a fair
normalized product ranking.

Defaults per operation and concurrency: `BENCH_SMALL_REQUESTS=10000` (4 KiB),
`BENCH_REQUESTS=256` (1 MiB), `BENCH_LARGE_REQUESTS=128` (4 MiB); concurrency 1
and 16. **41,536 timed requests, zero failures**. Use a clean lifecycle to rerun;
the benchmark refuses an already-populated benchmark prefix.

| Object | Clients | PUT ops/s | PUT MiB/s | PUT p50/p99 ms | GET ops/s | GET MiB/s | GET p50/p99 ms |
|---|---:|---:|---:|---|---:|---:|---|
| 4 KiB | 1 | 201.79 | 0.79 | 4.82 / 7.03 | 829.17 | 3.24 | 1.16 / 1.57 |
| 4 KiB | 16 | 572.63 | 2.24 | 28.69 / 46.81 | 1,511.65 | 5.90 | 8.04 / 39.67 |
| 1 MiB | 1 | 60.50 | 60.50 | 16.26 / 35.05 | 310.69 | 310.69 | 3.18 / 5.13 |
| 1 MiB | 16 | 222.21 | 222.21 | 69.05 / 110.11 | 995.36 | 995.36 | 14.38 / 32.82 |
| 4 MiB | 1 | 24.36 | 97.42 | 39.71 / 59.01 | 156.28 | 625.12 | 6.39 / 9.24 |
| 4 MiB | 16 | 112.78 | 451.14 | 127.73 / 262.81 | 375.95 | 1,503.82 | 32.71 / 79.39 |

Raw journals, native reports, latency distributions and manifests are retained
in ignored `results/verified/`; README values are observed summaries.

## Files and upstream references

```text
s3-cluster/
  Makefile
  docker-compose.yml
  config/                   # native server / gateway settings
  scripts/                  # topology-specific adapters and audits
  results/                  # ignored actual run evidence
../../object-storage/
  client/                   # Dockerfile, SDK workload and native audits
  fault.py
  benchmark.py
```

Inspected [1.0.5 source and release](https://github.com/rustfs/rustfs/tree/1.0.5),
[erasure coding](https://docs.rustfs.com/concepts/erasure-coding/),
[deployment documentation](https://docs.rustfs.com/installation/), native heal
command/status/outcome implementations and upstream disk-rebuild/chaos tests.
The pinned source's durability mode, shard-integrity flags and canonical heal
reports determine the harness checks; the external SDK oracle is independent.
