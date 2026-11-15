# SeaweedFS: S3 cluster and disk recovery

S3 over a separated master, volume and filer architecture with HA metadata.

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
completion, multipart abort and invalid Content-MD5 rejection. Ten negative
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
| process: volume0 | 1,034 / 1,034 | 0 | 0 | 489 | 0.01 s | 6.41 s | 11,167 |
| disk: volume0 | 1,256 / 1,256 | 0 | 0 | 480 | 0.00 s | 6.57 s | 12,423 |
| process: etcd0 | 1,014 / 1,014 | 0 | 0 | 478 | 0.01 s | 5.92 s | 13,437 |
| process: master0 | 521 / 519 | 2 | 0 | 0 | 31.03 s | 6.02 s | 13,957 |
| process: filer0 | 779 / 778 | 1 | 0 | 248 | 14.67 s | 6.45 s | 14,735 |
| process: s3api0 | 845 / 844 | 1 | 0 | 298 | 11.75 s | 6.93 s | 15,579 |

Every listed case passed: **zero lost acknowledged objects, corrupt bodies,
stale versions or unknown keys** after recovery. Outages lasted approximately
30–31 seconds. Ready time starts at restart; it excludes the outage and native
repair/full audit, so it is not total recovery time. An ambiguous response can
mean a committed PUT; it is not automatically data loss. Final post-benchmark
reconciliation verified **36,347 objects /
2,081,820,672 bytes** with all four error counters zero. Both
layered cleanup commands completed successfully, including bucket deletion.

## Topology and recovery gotchas

| Role | Count | CPU / RAM per service |
|---|---:|---|
| SeaweedFS 4.53 master | 3 | 0.5 / 512 MiB |
| Volume server | 9 | 1 / 512 MiB; three per logical datacenter |
| etcd 3.6.16 | 3 | 0.5 / 512 MiB; HA filer metadata |
| Filer | 3 | 1 / 768 MiB |
| S3 gateway | 3 | 1 / 512 MiB |
| HAProxy 3.2.8-alpine | 1 | 0.5 / 128 MiB |
| Docker-built Boto3 1.44.3 client | 1 | 2 / 2 GiB |

Store caps total 18.5 CPUs / 11.375 GiB; CPU caps exceed this host's 11 CPUs.
The `/buckets` rule explicitly uses `replication=200` and `fsync=true`: three
copies in different datacenters. `002` would put additional copies in the same
rack and does not express this failure domain. Native volumes are deliberately
small, 64 MiB, with 128 slots/server and a 1 GiB minimum free-space threshold.
These placement capacities are not Docker quotas or production sizing.

- `make disk-failure` destroys only `volume0`'s named data volume and recreates
  it empty. Native `volume.fix.replication -apply` runs under the shell lock;
  native topology checks require three distinct datacenters for each volume.
- `make metadata-failure`, `make control-failure`, `make filer-failure` and
  `make gateway-failure` separately kill etcd0, master0, filer0 and s3api0.
  `make role-failures` runs these four in sequence. The master outage produced
  **zero successful PUT responses during its 30 s window** in this run, despite
  a three-master topology. Do not describe that observation as uninterrupted
  writes or infer leader-independent allocation from Raft membership alone.
- `make replica-test` reads every required acknowledged object’s chunk directly from each owning volume
  server with `?proxied=true`, prohibiting forwarding. A negative control sends
  a request to a nonowner and requires 404. This prevents surviving replicas
  from masking a missing local copy. Every required object's bytes are
  independently checked in all three datacenters. The final audit made over
  109,000 direct replica chunk reads with zero mismatch (36,346 required objects /
  6,245,363,712 physical body bytes; one additional ambiguous object was
  included in the SDK reconciliation).
- Native `volume.check.disk -slow` compares needle IDs; it does not replace the
  independent complete-byte checks. The bounded plain corpus has no encrypted
  or manifest chunks; the audit rejects unexpected formats rather than guessing.
- etcd checks compare all three native `hashkv` values at the same committed
  revision. Metadata consistency and payload replication are separate checks.
- S3 gateways discover all three filers. HAProxy resolves recreated backend IPs
  through Docker DNS. A healthy frontend alone does not establish all role health.
- Losing a whole logical datacenter can block writes under strict `200`
  placement; only single-server failures were injected here. Metadata disk
  destruction, WAN partitions, correlated power loss and offsite restore remain
  separate tests. All datacenters still share one physical Docker disk.

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
| 4 KiB | 1 | 165.97 | 0.65 | 5.87 / 8.79 | 803.92 | 3.14 | 1.20 / 1.90 |
| 4 KiB | 16 | 655.47 | 2.56 | 25.02 / 41.86 | 1,483.34 | 5.79 | 9.06 / 34.66 |
| 1 MiB | 1 | 64.25 | 64.25 | 14.17 / 25.71 | 437.00 | 437.00 | 2.18 / 4.13 |
| 1 MiB | 16 | 185.71 | 185.71 | 31.57 / 1050.35 | 1,135.76 | 1,135.76 | 12.29 / 33.46 |
| 4 MiB | 1 | 19.72 | 78.88 | 44.52 / 261.74 | 202.58 | 810.33 | 4.72 / 7.37 |
| 4 MiB | 16 | 61.19 | 244.75 | 100.11 / 1094.17 | 394.80 | 1,579.20 | 25.11 / 86.78 |

Raw journals, native reports, latency distributions and manifests are retained
in ignored `results/verified/`; README values are observed summaries.

The small 64 MiB volume geometry is part of the measured path, including allocation/rollover. The approximately 1 s concurrent PUT p99 was observed; its cause was not profiled.

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

Inspected pinned 4.53 [production topology](https://github.com/seaweedfs/seaweedfs/blob/4.53/note/slides/seaweedfs-production-setup.md),
[`fs.configure`](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/shell/command_fs_configure.go),
[`volume.fix.replication`](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/shell/command_volume_fix_replication.go),
[`volume.check.disk`](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/shell/command_volume_check_disk.go),
[native volume reads](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/server/volume_server_handlers_read.go)
and [etcd filer metadata](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/filer/etcd/etcd_store.go).
