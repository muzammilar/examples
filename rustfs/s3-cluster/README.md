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

## Live production tests

`make test-production` runs the API suite, separate crashes of native members
`rustfs0` and `rustfs3`, permanent `rustfs0` drive replacement, a single-router
crash and full historical verification. Individual commands are `make failover
FAULT_TARGET=rustfsN`, `make disk-failure`, `make gateway-failure`, `make verify`,
`make repair`, `make cold-reopen` and `make report`. Cases have unique directories
under the default `results/recovery`; earlier histories are never truncated.
`FAULT_DURATION=900` is a safety ceiling, not a load duration or recovery proof.

Two separate Compose containers generate paced PUTs and independent GETs. Each
has 2 CPUs / 2 GiB, one outstanding request and a 50 ms delay. Every unique 32 KiB
PUT has a fsynced attempt and outcome outside the failed volume, with one SDK
attempt and no application replay. The reader checks old and current ACK bodies,
SHA-256, length and application version independently of write completion.
An immutable ACK returning 404 or wrong bytes fails the contract; transport/503
errors remain availability observations. Non-ACK writes are conservatively
classified as ambiguous; raw errors distinguish definite connection failures.

The existing maintained Boto3 checksum/S3 harness is retained as an explicit
Python-workload exception to the usual Go/Rust/C++ preference. Native signing
uses botocore SigV4. Controllers, filesystem hashing and layout-JSON checks use
the standard library; the example does not implement an S3 signer or erasure code.

### Recovery and proof boundaries

| Gate | Required evidence |
|---|---|
| Native readiness | All four views agree on deployment, disk UUIDs and exact pool/set/slot; EC2+2, one four-drive set, no offline/unknown drives, native read/write quorum, lock/IAM/pool readiness and bitrot self-test |
| Fault injection | Exact inspected process exits 137; disk loss also fences/removes its old container and deletes only its labelled volume after releasing the completed init job |
| Live repair | Fixed fsynced ACK prefix; both workload roles stay live through native repair, physical checks and complete prefix GETs; require further progress afterward |
| Remaining tail | Stop the writer only after the live barrier; keep the reader running through native repair and full final listing/body reconciliation |
| Survivors | Every unrelated native/router container ID, process start, PID and restart count stays unchanged |
| Physical census | Read-only native format/slot agreement and four `xl.meta` paths for every required key |
| Immutable payloads | Preserve every out-of-line `part.N` fingerprint for `data/`, `bench/` and immutable churn, across all four slot positions; require exact reconstruction |

Inline bodies are stored inside private `xl.meta`; four filenames are **not**
independent verification of four inline payloads. RustFS's protected deep-heal
implementation remains the inline/erasure decoder, and full external SHA-256 GETs
verify the logical bodies. Canonical completion requires `completed`, `complete`
coverage, zero failed/skipped/unknown outcomes and no counter overflow. Native
`unchanged` also includes metadata/absence dispositions; it is not a byte count.
The outcome's bounded detail window is not a complete per-object ledger. Native
retry counts remain recorded. Mutable `cases/` data-directory fingerprints are
excluded because legitimate overwrite/GC can retire old files; their complete
SDK bodies and native protected checks remain required.

Permanent replacement runs the supported native **erasure-set** heal for pool 0 /
set 0 before requiring a formatted member; a bucket heal alone cannot initialize
an empty drive. The native formatter restores the canonical logical slot UUID.
New volume creation time, old-owner fencing and reconstructed data prove the
physical replacement; a changed slot UUID is not required. Any native automatic
replacement records must be definitive/completed; explicit admin set repair has
its own retained token, scoped canonical outcome and independent data checks.
These are controller-driven native recovery steps, not a claim of fully automatic
repair. No locks, healing markers or `format.json` are manually removed/copied.
The set operation can finish with zero object outcomes when its captured bucket
scope is empty. That establishes no object coverage: a subsequent bucket-scoped
deep scan must process at least the fixed required prefix, alongside the complete
body and physical checks.

### Mounts, auditor and lifecycle

Each node mounts one labelled volume at `/data` and its `disk0` subdirectory
directly at `/data/disk0`. This preserves the native endpoint and makes the drive
an actual mount point. The native image otherwise creates anonymous `/data`
volumes; mounting only a parent directory also fails native replacement mount
admission. `make docker-up` completes `init_storage.py` before creating native services,
preparing each volume subdirectory for UID 10001 before its subpath mount is used.
No unsafe disk-check bypass is enabled. Logical nodes still share one physical
Docker filesystem/host; native admission is not independent-power certification.

The auditor has no network, a read-only root filesystem, read-only data and
witness mounts, and only `DAC_READ_SEARCH` with UID 0 to inspect native private
metadata. It emits JSON; the host controller saves evidence. Native permissions
are unchanged. The application and native admin tools run separately.

Local `s3-workload`, `benchmark.py` and `witness_archive.py` links point to tracked
source. `make build` materializes regular hard links/copies in ignored `.build/tools`
and builds from that local context; no sibling Makefile or generated state is used.
Repeated `make up` verifies the retained fixture. Full teardown checks zero owned
containers/volumes/networks/build tags and records a fixture-ID proof; the next
fresh `up` archives the matching retired witness and failed histories. Unexplained
storage/identity changes fail closed. `make recover-bootstrap` resumes only a
diagnosed fully seeded initialization, and `make recover-replacement
RECOVERY_CASE=<failed-disk-case>` verifies/rebinds a declared replacement without
PUT replay. Original failures remain failed.

Peer partitions, two-node/quorum loss, bounded pressure, live resource changes,
new-pool scaling/rebalance/decommission, upgrades/rollback, physical EIO/bitrot/power loss
and independent backup restore remain **unrun**. `make cold-reopen` checks retained
persistence through whole-stack stop/start, with no live-availability claim; OS
caches remain warm. It is not a cold-query benchmark.
It rejects running example-owned one-off workload/check containers and compares
the pre-stop immutable payload fingerprints without first refreshing them.

## Diagnosed setup and recovery findings (2026-11-16–17)

The initial read-only auditor could not read native private format metadata as
the host UID. Its startup remained failed after **10,128 seed objects**; the
same seed was verified through the isolated read-only auditor without replaying
PUTs. Native data permissions were retained.

The first permanent-drive test also remains **failed**. Native info reported
four online/`ok` drives while the replacement UUID was empty and its physical
`format.json` absent. The stronger membership/format gate refused that state.
The first admin set-heal attempt then stopped with `untraversable` / partial
coverage and `Replacement target is not ready: target mount admission failed`.
No file/marker was removed to override the refusal.

The corrected target-only binding mounted the existing `disk0` subdirectory
directly, retaining its volume, bytes and endpoint and every other process.
Native set repair then completed. All **6,045 ACK objects across three histories**
survived; all **28 non-ACK attempts** were absent. Full readback checked **16,178
objects / 408,915,968 bytes**, with **64,712 physical metadata paths** and all
**512 retained immutable payload fingerprints** matching. The original failed
case and refused repair remain failed evidence. Applying the same binding to the
other nodes preserved all data/volume births/UUIDs; that configuration repair
had no live writer and does not qualify live rolling maintenance.

The next disk case also remains failed: the controller incorrectly required the
set-format operation's object counter to cover the ACK prefix. That operation
completed with zero object outcomes; the separate bucket deep scan subsequently
processed all **20,839 objects** without unresolved outcomes. The corrected
controller distinguishes these scopes and retains the strict minimum count for
the bucket scan. Recovery of the same fixture verified **10,706 cumulative ACK
objects across six histories**, **20,839 complete bodies / 561,647,616 bytes**,
**83,356 metadata paths** and all **512 retained immutable payload fingerprints**.
No application PUT was replayed; the original failed reports remain failed.

## Retained recovery results (2026-11-16–17)

Four latest successful cases on the same retained fixture are shown below.
The process cases preceded the format-scope correction described above; the disk
and gateway cases used the corrected native checker. Final-source fresh-run
qualification is a separate check. Each fault preserves all four
unrelated service process identities. These are paced recovery tests, not capacity.

| Injection | ACK PUTs | Non-ACK PUTs | Successful GETs | GET errors | Maximum successful-read gap |
|---|---:|---:|---:|---:|---:|
| Native member 0 crash | 1,640 | 9 | 3,483 | 23 | 2.949 s |
| Native member 3 crash | 1,785 | 5 | 3,622 | 15 | 12.134 s |
| Permanent member 0 disk loss | 3,251 | 10 | 5,594 | 27 | 12.125 s |
| Single gateway crash | 1,953 | 442 | 4,370 | 394 | 30.409 s |

The retained history contains **15,910 ACKs / 16,413 attempted PUTs across eight
histories**, including the two failed cases whose recovery is documented above.
After the benchmark, full reconciliation verified **46,811 objects /
2,424,705,024 bytes**, with zero missing ACKs, corrupt bodies, stale versions or
unknown keys. Native census checked **187,244 metadata paths** and **3,584
immutable payload fingerprints**; deep scan had zero failed/skipped/unknown
outcomes. All 503 non-ACK attempts were absent at that reconciliation.

The disk case completed in **418.669 s** including prechecks, outage and final
verification. Its live barrier checked **21,373 required objects / 85,492
metadata paths** and all 512 pre-fault payload fingerprints with both roles
running. Prefix GET readback took **15.815 s**; final tail readback checked
24,090 objects. The 5.805 s native process-ready time excludes data repair.

The gateway case completed in **378.755 s**. Its 30-second outage demonstrates
the single-router availability boundary. Request failures stay in the report;
zero lost ACKs does not mean uninterrupted service.

Initial whole-stack stop/start retained the five container identities and native
volume/slot identities and verified all 46,811 bodies in **253.294 s**. This
initial helper refreshed raw payload references after restart; the corrected
helper retains and compares the pre-stop references instead.
It remains a persistence test with warm OS caches, not an HA or cold-query run.

## Fresh lifecycle qualification (2026-11-17)

Both fresh-volume startups passed the default five-command workflow plus native
verification. The first fixture checked **10,133 objects / 210,833,408 bytes**,
**40,532 metadata paths** and **512 immutable payload fingerprints**. Its full
cleanup left zero owned resources/build tags. Before fresh setup, retiring the
larger retained fixture deleted **46,811 objects in 317.244 s**.

The second fixture passed all **35 unit/negative controls** and the complete
**`make test-production` aggregate in 1,035.312 s**. All four cases below used
the final controller, native checker and Compose configuration. Each retained
the other four infrastructure process identities and kept both workload roles
live through the repair barrier.

| Fresh injection | ACK PUTs | Non-ACK PUTs | Successful GETs | GET errors | Maximum successful-read gap |
|---|---:|---:|---:|---:|---:|
| Member 0 crash | 1,294 | 5 | 2,500 | 9 | 12.125 s |
| Member 3 crash | 1,379 | 7 | 2,656 | 14 | 12.120 s |
| Permanent disk loss | 2,258 | 10 | 3,746 | 27 | 12.124 s |
| Gateway crash | 1,296 | 440 | 2,804 | 391 | 30.466 s |

Final full readback verified **16,360 objects / 414,879,744 bytes**, including
**6,227 cumulative ACKs from 6,689 attempts across four histories**, with zero
missing/corrupt/stale/unknown objects. All 462 non-ACK attempts were absent.
Native physical proof checked **65,440 metadata paths** and all **512 payload
fingerprints**. The corrected whole-stack reopen passed in **86.971 s**
(87.765 s including its launcher): pre-stop and post-reopen fingerprint maps
were identical, and all five containers restarted with unchanged volume/native
identities. A controlled live one-off admission probe was rejected without
shutdown; idle admission passed after its scoped cleanup.

Both fresh fixture IDs differ from their predecessor. Retired manifest/history
and failed-case status hashes matched after archival; the final offline reporter
also validated the larger archived fixture while retaining both original failure
labels. Six additional offline evidence probes rejected missing ACKs, stale
current proofs, wrong deployments, incomplete scans and mismatched golden-file
counts while allowing genuinely pre-fault reference timestamps.


| Lifecycle command | Fresh cycle 1 | Fresh cycle 2 |
|---|---:|---:|
| `make docker-up` | 13.813 s | 12.055 s |
| `make up` | 74.381 s | 71.310 s |
| `make test` | 9.515 s | 9.552 s |
| `make down` | 31.753 s | 72.214 s |
| `make docker-down` | 1.190 s | 1.601 s |

Additional verification/aggregate/reopen times are separate from these five
command timings. All **three** scoped teardowns left zero owned containers,
volumes, networks and build tags. Exactly the original **ten unrelated Docker
volumes**, with unchanged creation times/labels, remained; no anonymous leaks.

## Previous recovery (2026-11-15)

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
| Docker-built Boto3 1.44.3 workload/verifier | 2 live roles + 1 checker during repair | 2 / 2 GiB each |

Store caps total 8.5 CPUs / 6.125 GiB. The four-drive erasure set explicitly
uses **EC:2 (2 data + 2 parity)** with strict durability. Source-derived read
quorum is 2 and write quorum 3; one unavailable node leaves three writable
members. Two concurrent failures were not injected or qualified here.
The two live workload roles plus a checker can oversubscribe this 11-CPU Docker
VM; paced fault results are availability/recovery observations, not capacity.
Benchmarks run one workload container with no live fault roles.

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

Measured **2026-11-17**, Apple M4 Pro; Docker Linux aarch64, **11 CPUs /
24.42 GiB**, Docker 29.5.3. Only this storage SUT was running. Store caps total
**8.5 CPUs / 6.125 GiB**; the one benchmark workload has **2 CPUs / 2 GiB**.
This is one warm run after the retained recovery history, with no live fault roles.

`make benchmark` completed **41,536 timed requests with zero failures** in
**123.739 seconds** including the launcher. Each operation/concurrency uses
`BENCH_SMALL_REQUESTS=10000`, `BENCH_REQUESTS=256`, `BENCH_LARGE_REQUESTS=128`.
Unique incompressible bodies are prepared outside timing; signing, transport,
SDK checksums and full GET hashing are timed. GET follows PUT and is warm;
OS caches were not cleared. There are no application mutation retries.

| Object | Clients | PUT ops/s | PUT MiB/s | PUT p50/p99 ms | GET ops/s | GET MiB/s | GET p50/p99 ms |
|---|---:|---:|---:|---|---:|---:|---|
| 4 KiB | 1 | 165.38 | 0.65 | 5.67 / 11.40 | 730.12 | 2.85 | 1.27 / 2.43 |
| 4 KiB | 16 | 507.79 | 1.98 | 31.00 / 66.10 | 1410.17 | 5.51 | 8.70 / 42.57 |
| 1 MiB | 1 | 52.10 | 52.10 | 18.37 / 35.78 | 266.89 | 266.89 | 3.55 / 5.41 |
| 1 MiB | 16 | 177.52 | 177.52 | 83.42 / 219.35 | 673.81 | 673.81 | 20.17 / 50.93 |
| 4 MiB | 1 | 18.46 | 73.84 | 45.60 / 241.07 | 114.70 | 458.81 | 7.50 / 25.55 |
| 4 MiB | 16 | 74.11 | 296.44 | 151.80 / 562.47 | 379.12 | 1516.49 | 34.55 / 61.43 |

These observations are separate from the earlier run below. Small 128/256-sample
p99s and shared-host scheduling do not establish production capacity or a
normalized ranking against the other storage systems.

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
  control.py                 # live failure/native recovery controller
  traffic.py                 # independent writer and reader
  native_api.py              # signed native membership/heal requests
  native_health.py           # pinned health/canonical-outcome gates
  physical.py                # read-only slot/metadata/payload census
  init_storage.py
  cold_reopen.py
  report.py                  # saved evidence; no live database access
  test_{control,native,physical,reader,teardown,traffic}.py
  prepare_build.py
  docker/Dockerfile.tools
  config/haproxy.cfg
  s3-workload -> ../../object-storage/client
  benchmark.py -> ../../object-storage/benchmark.py
  witness_archive.py -> ../../object-storage/client/run_state.py
  .build/tools/              # ignored regular-file Docker context
  results/                   # ignored witnesses, histories and diagnostics
```

Inspected [1.0.5 source and release](https://github.com/rustfs/rustfs/tree/1.0.5),
[erasure coding](https://docs.rustfs.com/concepts/erasure-coding/),
[deployment documentation](https://docs.rustfs.com/installation/), native heal
command/status/outcome implementations and upstream disk-rebuild/chaos tests.
The pinned source's durability mode, shard-integrity flags and canonical heal
reports determine the harness checks; the external SDK oracle is independent.


Pinned native recovery references:
[cluster/drive operations](https://github.com/rustfs/rustfs/blob/1.0.5/docs/operations/cluster-lifecycle-operations.md),
[replacement mount admission](https://github.com/rustfs/rustfs/blob/1.0.5/crates/heal/src/heal/replacement_readiness.rs),
[canonical slot format restoration](https://github.com/rustfs/rustfs/blob/1.0.5/crates/ecstore/src/layout/set_heal.rs),
[canonical outcome semantics](https://github.com/rustfs/rustfs/blob/1.0.5/crates/heal/src/heal/outcome.rs),
[replacement completion records](https://github.com/rustfs/rustfs/blob/1.0.5/crates/heal/src/heal/resume/replacement.rs)
and [protected integrity workflow](https://github.com/rustfs/rustfs/blob/1.0.5/docs/operations/shard-integrity-audit.md).

## Future work / TODO

Deferred checks for supported configurations; these are **not executed coverage**.
Keep recorded passes, failures and limitations above. Performance work requires
a separately selected depth; this list does not restart paused experiments.

- [ ] Exercise peer partitions/quorum loss and supported pool/resource changes; verify erasure placement and complete native healing.
- [ ] Test supported rolling upgrades and only supported rollback paths, recording format compatibility limits.
- [ ] Inject bounded pressure, device write errors and controlled bit corruption on owned storage; distinguish these from process or volume loss.
- [ ] Restore an independent backup and reconcile every acknowledged object body/version.

## File organization

- [`scripts/`](scripts/): lifecycle, fault and report controllers.
- [`tests/`](tests/): offline controller tests.
- [`config/`](config/): contracts and configuration.

The Makefile is the entry point. Generated builds and raw evidence stay in
ignored directories; the tracked reports do not replace those raw histories.
