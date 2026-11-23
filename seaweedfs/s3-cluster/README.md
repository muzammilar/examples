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
completion, multipart abort and invalid Content-MD5 rejection. Eleven shared negative
controls test corruption, truncation, stale data, unavailable responses, missing
ACKs, pagination, partial cleanup errors and preserving the historical oracle.

`make failover` and `make disk-failure` are separate experiments. A continuous
32 KiB producer runs through the outage and repair. Its fsynced attempt/ACK
journal lives outside the store. There is one SDK attempt per request; ambiguous
outcomes are reported and checked against the attempted body, never blindly
replayed. Every acknowledged object must survive with exact SHA-256, length and
application version. All earlier fault journals are rechecked on subsequent runs.

## Production-test backfill

`make test-production` runs the API suite, volume-process crash, permanent
volume loss/replacement, metadata leader crash, master leader crash, filer and
S3-gateway crashes serially, then reconciles all histories. Individual targets
remain `failover`, `disk-failure`, `metadata-failure`, `control-failure`,
`filer-failure`, `gateway-failure` and `verify`. For the rebuilt-copy check, repeat `make failover FAULT_TARGET=<replaced-node>`
after `make disk-failure`. `make cold-reopen`, `make benchmark` and `make report`
are separate targets. Run them against the same
`RESULTS_DIR` (default `results/recovery`); every fault creates a unique directory.

The writer and reader run in **separate Compose tools containers**, each capped
at 2 CPUs / 2 GiB. The writer sends unique 32 KiB objects with one SDK attempt,
a 50 ms pacing delay and fsynced attempt/outcome records. There are no mutation
retries. The independent reader alternates prior witnessed bodies with current
ACK bodies, using full SHA-256, length and version checks; a stalled PUT cannot
block its GET loop. The existing Boto3 harness is retained deliberately to keep
the S3 compatibility and checksum oracle consistent with the other storage
examples. This Python SDK workload is an exception to the usual Go/Rust/C++
application preference; orchestration and native audit code also use Python.

Both processes must demonstrate progress before injection and after native
repair. They remain alive through the deliberate 30 s outage, restart and native
three-zone repair barrier. Then the writer stops; the reader continues during
complete S3 body, direct replica-byte and current-revision etcd checks. A wrong
successful read fails the case immediately. Transient GET errors, including 404,
are recorded as availability failures, not successful reads. Every earlier
attempt/ACK is reconstructed from append-only journals, including failed cases;
ambiguous and interrupted PUTs may exist only with their original expected body.

The controller discovers native master/etcd leadership and checks it again before
injection. Volume failures discover a nonempty native owner (an explicit `FAULT_TARGET`
may select another allowlisted volume server); injection rechecks ownership.
An idle server is never counted as a data-failure test.
Every crash proves exit 137 on the exact inspected container. Permanent loss also
requires a healthy runtime listing proving the old owner absent before deleting
its labelled data volume. All 21 unrelated containers must retain their ID,
start time, PID and restart count. Native placement/repair, every required chunk's
three direct copies and metadata agreement are checked again after recovery.
A capacity guard refuses injection with less than 6 GiB free in Docker; this is
not an isolated disk-pressure experiment.

Raw request events record wall-clock completion times and monotonic request
latencies. `result.json` separates successful/failed PUT/GET counts, rates and
nearest-rank p50/p99 by baseline, injection, outage, repair and after phases.
`after` includes final reconciliation after the writer stops; its rate is not a
steady-state throughput benchmark. The reader/writer each make one request at a
time and sleep 50 ms; their observed gaps include request time, fsync and pacing.
`FAULT_DURATION=600` is a 60–1,800 s safety ceiling, not a target duration; either
process reaching it before the controller finishes fails the case.

Fixture checks bind the external oracle to exact project-owned named-volume
creation times and to anonymous volumes mounted by inspected owned containers.
The pinned upstream image creates anonymous `/data` mounts even for stateless
filers/gateways. Repeating `make up` against the same fixture verifies retained
data instead of overwriting it. Changing a fixture's storage without an explicit
recovery proof fails closed. `make docker-down` records a fixture-ID/project proof
only after verifying zero owned containers, labelled volumes, exact witnessed
anonymous volumes, networks and build tags. The next `up` requires that matching
proof before archiving the old fixture, manifests, reconciliation and fault
histories under ignored `previous-runs/`; unrelated JSON/log files stay in place.
A mismatched proof or residual witnessed volume fails closed. Cleanup never prunes
unrelated anonymous volumes. Use the complete five commands for a fresh run;
`down` alone removes only the bucket/workload.

The host controller's local `witness_archive.py` symlink exposes the tracked
`../../object-storage/client/run_state.py` helper. It uses no sibling service,
Makefile or generated state; materialized Docker source and raw evidence remain
ignored.

Scaling/draining, peer partitions, quorum loss, memory/isolated-disk pressure,
version upgrades, metadata disk destruction, physical EIO/power loss and an
independent backup restore are **not qualified** by this matrix.

`make cold-reopen` separately stops/starts the entire owned stack with retained
volumes and reconciles all historical bodies; no requests run during that
full-shutdown persistence test, and it makes no availability claim. Docker VM
filesystem caches remain warm; this is not a cold-query benchmark. Recrashing
the rebuilt member was qualified separately below.

All zones and witnesses still share this host; external-to-container journals
do not survive loss of the host itself.

## Repeatable default lifecycle (2026-11-16)

Two complete five-command cycles used the unchanged default `results/recovery`,
with an additional `make verify` before teardown. Apple M4 Pro, Docker 29.5.3
Linux aarch64, 11 CPUs / 24.42 GiB RAM; the same service caps below. No other
storage SUT ran. These are lifecycle timings, not capacity benchmarks.

| Cycle | docker-up | up | test | verify | down | docker-down |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 20.691 s | 18.675 s | 9.591 s | 22.985 s | 10.391 s | 3.454 s |
| 2 | 21.553 s | 18.771 s | 9.507 s | 22.944 s | 10.230 s | 3.511 s |

Each cycle verified **10,133 objects / 210,833,408 bytes**, zero missing/corrupt/
stale/unknown results, **30,417 direct replica chunk reads / 632,500,224 physical
body bytes**, and current-revision agreement on all three etcd members. Native
placement reported **six logical volumes / 18 zone copies**, zero missing zone
replicas. Both cleanups left **zero owned containers, named/anonymous volumes,
networks or build tags**; the same **ten unrelated volumes** remained untouched.
The second fixture has a new UUID and preserves the first oracle/reconciliation
and successful teardown proof in `previous-runs/`.

**26 SDK/controller controls passed**, including four teardown/archive controls
for wrong fixture IDs, residual anonymous mounts, preservation of failed histories
and unrelated files, and exclusion of unrelated volumes from cleanup. Ruff passed.
Raw lifecycle logs and proofs are in ignored `results/recovery/`. This change
qualifies repeatable teardown/recreation; the earlier live faults, cold reopen
and throughput measurements below were not rerun for this lifecycle-only fix.

## Measured independent-reader recovery (2026-11-16)

Apple M4 Pro; Docker 29.5.3 Linux aarch64, 11 CPUs / 24.42 GiB RAM.
Only this storage SUT was running. Store caps remain 18.5 CPUs / 11.375 GiB;
CPU limits exceed the host CPU count. Two concurrent traffic containers add
4 CPUs / 4 GiB; native audit tools can add another 2 CPUs / 2 GiB.
These are quotas on one shared VM, not dedicated machines or failure domains.

| Injection | ACKs / ambiguous PUTs | GETs / errors | Outage ACKs / GETs | First ACK after exit | Longest GET completion gap | Exit → native replica barrier |
|---|---:|---:|---:|---:|---:|---:|
| process: volume0 | 930 / 0 | 1,487 / 0 | 484 / 541 | 0.023 s | 0.552 s | 51.602 s |
| disk: volume0 | 1,178 / 0 | 1,818 / 0 | 486 / 545 | 0.019 s | 0.563 s | 66.951 s |
| process: etcd2 | 930 / 0 | 1,565 / 0 | 479 / 531 | 1.003 s | 1.042 s | 51.476 s |
| process: master0 | 503 / 2 | 1,573 / 0 | 52 / 515 | 26.936 s | 0.560 s | 51.653 s |
| process: filer0 | 689 / 1 | 1,606 / 0 | 241 / 527 | 15.099 s | 0.558 s | 51.486 s |
| process: s3api0 | 736 / 1 | 1,468 / 1 | 287 / 359 | 0.049 s | 10.123 s | 51.519 s |
| process: volume0 (rebuilt member) | 935 / 0 | 1,694 / 0 | 487 / 546 | 0.035 s | 0.620 s | 51.544 s |

Fault time uses Docker’s recorded SIGKILL exit time, checked against the host phase
markers. The outage window ends when restart begins; it includes disk fencing/
removal in the disk case. Native-barrier time includes the deliberate 30 s hold,
restart and native repair, but excludes subsequent full byte auditing. Service
restart-to-ready was 5.740–5.832 s; Ready alone was never accepted as repair.
The gateway error was HTTP 503 after a 10.012 s GET. The master leader outage
delayed the next PUT ACK by 26.936 s despite three masters. Neither case provides
uninterrupted service. Raw per-phase success/failure p50/p99 and sample counts
are available through `make report` and each saved `result.json`.

Across **eight retained histories**, including the failed pre-injection attempt,
**5,991 acknowledged churn objects / 5,995 attempts** survived.
All seven qualified cases preserved all 21 unrelated container/main-process
identities. The rebuilt member was crashed again with its new volume retained; every required
physical copy still matched afterward. Of four ambiguous PUTs, one was present
with its exact expected body and three were absent; none is counted as an ACK.

Post-benchmark reconciliation checked **36,893 objects / 2,099,712,000 bytes**:
zero missing acknowledged keys, corrupt bodies, stale versions or unknown/
duplicate keys. The native oracle checked **36,892 required objects**, including
one empty inline object, with **110,694 direct chunk reads / 6,299,037,696 physical
body bytes**, zero mismatch and the nonowner-404 negative control. Native inventory
finished with 45 logical volumes / 135 zone copies. Current-revision etcd hashes
matched across all three members. Saved native headers from the earlier runs
also passed the strengthened current-revision validation.

Full-stack cold reopen retained all 22 container identities and storage volumes,
restarted every process, then passed the same historical byte/metadata checks in
**108.113 s**. Repeated `make up` verified retained state. A second fresh-volume
five-command lifecycle passed the complete **10,133-object / 210,833,408-byte**
API suite and all **30,417 direct replica chunk reads**, with zero error counters.
The fresh fixture also passed owner-selected crash and disk-loss runs with **2,117
additional ACKs across two histories**, zero ambiguous PUTs, read errors or loss.
All historical bodies and three copies were checked again before cleanup.
Both cleanup passes proved **zero owned containers, named/anonymous volumes,
networks or built image tags**; unrelated Docker resources and raw journals remain.

Original failures remain evidence: the first setup seeded 10,128 objects before
an overly strict ownership guard rejected image-created anonymous mounts. All
S3 bodies, three-copy bytes and metadata were audited before binding that exact
fixture; no seed was repeated or reset. A controller variable-shadowing error
next stopped before injection after 90 ACKs. Those objects remain in every later
reconciliation, and that case remains failed. Fresh native verification later
found a legitimate null volume list on an empty server; both auditors now accept
empty members while rejecting an entirely empty cluster. That failed log remains.
Final source checks passed 22 unique SDK/controller negative and correctness controls, Docker build gates,
Python compilation and Ruff.

### Post-recovery benchmark

Fresh unique prefixes on the retained recovered fixture; generation outside the
counter, SDK signing/checksums/transfer and complete GET hashing inside. No
mutation retries. GET immediately followed PUT and is warm. Native volume size
remained 64 MiB; allocation/rollover is part of the path. One measured run,
**41,536 timed requests / zero request failures**; this is a local SDK throughput
comparison point, not a normalized product ranking or cold-cache measurement.

| Object | Clients | PUT ops/s | PUT MiB/s | PUT p50/p99 ms | GET ops/s | GET MiB/s | GET p50/p99 ms |
|---|---:|---:|---:|---|---:|---:|---|
| 4 KiB | 1 | 172.58 | 0.67 | 5.66 / 8.35 | 792.12 | 3.09 | 1.21 / 1.94 |
| 4 KiB | 16 | 645.07 | 2.52 | 25.52 / 42.51 | 1455.12 | 5.68 | 9.13 / 35.75 |
| 1 MiB | 1 | 66.87 | 66.87 | 14.46 / 27.13 | 441.59 | 441.59 | 2.14 / 3.77 |
| 1 MiB | 16 | 175.64 | 175.64 | 39.55 / 1061.18 | 1135.86 | 1135.86 | 11.86 / 29.83 |
| 4 MiB | 1 | 19.04 | 76.16 | 46.29 / 258.83 | 200.27 | 801.09 | 4.70 / 9.04 |
| 4 MiB | 16 | 50.49 | 201.97 | 213.87 / 1090.41 | 435.15 | 1740.61 | 27.84 / 80.57 |

Concurrent PUT p99 again reached about 1.09 s. Its cause was not profiled.
Raw evidence is under `results/production-20261116/` and
`results/lifecycle-20261116/` (ignored). `make report RESULTS_DIR=...` reads the
preserved evidence after cleanup and rejects stale final verification.

## Previous recovery measurements (2026-11-15)

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

- `make disk-failure` destroys only the selected nonempty server's owned data
  volume (`volume0` in these measurements) and recreates it empty. Native `volume.fix.replication -apply` runs under the shell lock;
  native topology checks require three distinct datacenters for each volume.
- `make metadata-failure` and `make control-failure` discover the elected etcd
  and master leaders. `make filer-failure` and `make gateway-failure` kill filer0
  and s3api0. `make role-failures` runs these serially. The 2026-11-15 fixed-master0
  outage had zero PUT ACKs during its 30 s hold; the latest elected-master run
  recovered its next ACK after 26.936 s. Neither establishes uninterrupted writes.
- `make replica-test` reads every required acknowledged object’s chunk directly from each owning volume
  server with `?proxied=true`, prohibiting forwarding. A negative control sends
  a request to a nonowner and requires 404. This prevents surviving replicas
  from masking a missing local copy. Every required object's bytes are
  independently checked in all three datacenters. The 2026-11-15 audit made over
  109,000 direct replica chunk reads with zero mismatch (36,346 required objects /
  6,245,363,712 physical body bytes; one additional ambiguous object was
  included in the SDK reconciliation).
- Native `volume.check.disk -slow` compares needle IDs; it does not replace the
  independent complete-byte checks. The bounded plain corpus has no encrypted
  or manifest chunks; the audit rejects unexpected formats rather than guessing.
- etcd checks compare all three native `hashkv` values at the same committed
  revision that is current on all three members. Metadata consistency and payload
  replication are separate checks.
- S3 gateways discover all three filers. HAProxy resolves recreated backend IPs
  through Docker DNS. A healthy frontend alone does not establish all role health.
- Losing a whole logical datacenter can block writes under strict `200`
  placement; only single-server failures were injected here. Metadata disk
  destruction, WAN partitions, correlated power loss and offsite restore remain
  separate tests. All datacenters still share one physical Docker disk.

## Previous benchmark (2026-11-15)

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
  control.py                # example-owned fault/lifecycle controller
  test_teardown.py
  witness_archive.py -> ../../object-storage/client/run_state.py
  traffic.py                # independent writer and reader
  cold_reopen.py            # retained-storage full-shutdown persistence
  witness.py                # strict attempt/outcome journal parsing
  report.py                 # summarize saved evidence without Docker
  test_control.py
  test_traffic.py
  test_native.py
  test_metadata.py
  prepare_build.py           # local hard-link/copy materialization
  docker/Dockerfile.tools
  s3-workload -> ../../object-storage/client
  benchmark.py -> ../../object-storage/benchmark.py
  config/                   # native server / gateway settings
  scripts/metadata_audit.py # three-member current-revision native etcd check
  .build/tools/              # ignored regular-file Docker context
  results/                  # ignored actual run evidence
```

Inspected pinned 4.53 [production topology](https://github.com/seaweedfs/seaweedfs/blob/4.53/note/slides/seaweedfs-production-setup.md),
[`fs.configure`](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/shell/command_fs_configure.go),
[`filer write/fsync propagation`](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/server/filer_server_handlers_write.go),
[`volume.fix.replication`](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/shell/command_volume_fix_replication.go),
[`volume.check.disk`](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/shell/command_volume_check_disk.go),
[native volume reads](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/server/volume_server_handlers_read.go)
and [etcd filer metadata](https://github.com/seaweedfs/seaweedfs/blob/4.53/weed/filer/etcd/etcd_store.go).

The two symlinks point only to repository-tracked source. `make build` materializes
`./s3-workload` as regular files in `.build/tools` (hard links where possible,
copies otherwise), adds this example's traffic/witness modules and local
Dockerfile, then builds from that local context. Neither `.build/` nor results
are committed; a clean checkout needs no sibling runtime or generated state.
The other S3 examples keep their existing controllers until separately tested.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
