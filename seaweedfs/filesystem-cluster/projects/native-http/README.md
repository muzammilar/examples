# SeaweedFS native HTTP access

This project shares the parent filesystem cluster and lifecycle. It adds a
separate, unprivileged C++/libcurl workload container. It uses only the local
filers' native HTTP API: **no AWS SDK, S3 gateway, AWS account/configuration or
remote bucket**. Endpoints are allowlisted, proxies and redirects are disabled,
and the service stays on the example's internal Compose network.

## Quick start

Run from `seaweedfs/filesystem-cluster/`:

1. `make docker-up` — prepare the owned storage infrastructure.
2. `make up NATIVE_HTTP=1` — install the FUSE and native HTTP applications.
3. `make test NATIVE_HTTP=1` — independently verify both baseline corpora.
4. `make down` — reconcile all active project witnesses and remove workloads.
5. `make docker-down` — destroy only this example's infrastructure/state.

For an existing parent baseline, use `make api-up`, then `make api-test`.
Use `make api-verify` for retained data. These projects share one backend for
matched comparisons; they are not independent examples sharing an unrelated lab.

## Contract and layout

`workload/` owns the CMake build, Dockerfile and application. `http_project.py` exposes
this project's targets; it uses only the parent lab's explicit Compose lifecycle.
HTTP journals live in ignored `results/native-http/journals/`. The parent native
oracle independently validates the bodies and all three owning volume copies.
Cleanup verifies HTTP and FUSE paths and retires both fixture indexes together.

The baseline uses identical SHAKE256 bodies/run IDs to the FUSE corpus, under a
separate `/native-http/` namespace. A successful PUT must return a valid size
receipt; transport/protocol errors remain ambiguous and are never replayed.
The URL requests `fsync=true`, RF `200` and the same collection. That content-flush
option does not make HTTP PUT equivalent to a filesystem rename/directory fsync;
report the different publication semantics rather than comparing weaker settings
as equivalent durability. Every ACK is fsynced outside the storage backend.

One persistent libcurl handle reuses connections per process. Connect timeout is
2 seconds and total request timeout 10 seconds, with no application mutation retry.
Readers verify every byte/hash through both filers. `make api-filer-failure`
keeps an HTTP writer and historical reader live while both filers are killed
for ten seconds, then reconciles all ACKs and ambiguous outcomes without reseeding.
`make api-test-production` runs verification, that fault and verification serially.
HTTP 200 containing an error or invalid receipt is not an ACK. Runtime source hashes and libcurl versions are
printed. No native gRPC or S3 path is being claimed by this project.

## Current status and approved performance scope

The C++ client passed runtime correctness through both filers and all three
native volume copies. Live HTTP writer/reader recovery after both filers were
killed for ten seconds passed: **27 new ACKs**, **30 ambiguous final paths confirmed
absent**, **9 historical read cycles / 20 RPC-error cycles**, zero wrong bodies.
Resumed-progress checks completed **16.562 seconds** after injection; this includes
startup/readiness waits and several successful operations, not exact RTO.

The initial 1,000-file FUSE comparison exceeded the 180-second supervisor after
802 ACKs and one in-flight operation. Independent readback found that last
operation **published despite lacking an ACK**; its observed body is now required
in every later audit. It is a **failed comparison**, [retained separately](reports/LARGE-TRIAL-TIMEOUT.json);
it is not a passed 1,000-file performance result. Publication stalls
near one second were frequent despite a much smaller median. The bounded
follow-up used three **256-file** pairs and passed final reconciliation.
No data was reset between these cases.
The user selected **level 3** for this comparison: three bounded, paired fresh
process trials, identical payloads and 256 files × 256 KiB per path per trial
(`COMPARE_FILES=256`), alternating execution order. Observe recent-write and
fresh-client reads;
do not call a new connection a physically cold disk cache.

Include all backend/client CPU, memory, disk bytes/I/O and owned storage, with
explicit compression/measurement gaps. No simultaneous heavy lab may run during
the comparison. This controlled API-versus-filesystem workload does not establish
general POSIX correctness or a universal production performance winner.

## Results — 2026-11-27

[Complete paired measurements, fault evidence and resource scopes](reports/COMPARISON-2026-11-27.json).
Each path wrote/read 768 matched trial files, **201,326,592 bytes** total.
The execution order was FUSE/HTTP, HTTP/FUSE, FUSE/HTTP. All bodies were checked
exactly through both filers and all three owning volume endpoints afterwards.

| Path | Publish ops/s, median (min–max) | Read ops/s, median (min–max) | Publish p50 / p95 across trials, ms | Read p50 / p95 across trials, ms |
|---|---:|---:|---:|---:|
| FUSE | 4.323 (3.893–4.879) | 334.291 (311.255–350.852) | 14.62–16.31 / 1,028.74–1,035.20 | 1.40–1.54 / 6.42–6.94 |
| Native HTTP | 131.062 (120.152–134.406) | 1,248.498 (1,241.481–1,264.148) | 7.21–7.39 / 8.56–8.81 | 0.762–0.767 / 0.970–1.100 |

These are **timed-operation rates**, excluding generation, local witness fsync,
startup and orchestration. Read timing includes request/open, complete body and
hash verification; deterministic expected-body generation happens before timing
for both paths. IQR and p99 observations are in the report. With only 256 samples
per cell, tail estimates are fragile; p99.9, saturation and capacity are unqualified.
There was no explicit cache eviction; fresh processes are not physical cold reads.

HTTP was faster for this implemented blob workload. This does **not** isolate
FUSE overhead: HTTP proxies bytes through a filer and publishes a size-checked
PUT with `fsync=true`; FUSE uses metadata RPC/direct-volume I/O and
create/file-fsync/close/rename/directory-fsync. HTTP does not supply equivalent
POSIX rename, locking or open-file semantics. The repeated approximately one-second
FUSE publication stalls remain unresolved; do not attribute them to a particular
component without tracing it. Both mount daemons remained running in every cell.

| Resource scope, entire paired comparison and verification | Observed value |
|---|---|
| Sampled owned-stack CPU | 76.950 core-seconds |
| Mean service CPU utilization, percent of one core | HTTP application container 1.07%; FUSE writer/mount 3.58%, reader/mount 5.43%; other roles reported separately |
| Sampled aggregate container-memory peak | 3,709,091,840 bytes, includes caches |
| Application peak RSS | FUSE writes 6,888–6,952 KiB / reads 7,400–7,432 KiB; HTTP writes 12,756–12,768 KiB / reads 13,268–13,364 KiB |
| Application CPU, user+system per 256 operations | FUSE writes 0.177–0.197 s / reads 0.146–0.148 s; HTTP writes 0.128–0.132 s / reads 0.153–0.155 s |
| Docker block bytes, all sampled roles | 1,819,250,688 read / 2,624,446,464 write; Docker I/O operation counts unavailable |
| Available native cgroup I/O | 1,821,540,352 read bytes / 7,217 reads; 2,538,274,816 write bytes / 19,403 writes; excludes shell-less etcd roles |
| Owned volumes allocated after verification | 2,455,404,544 bytes; includes all retained histories and metadata |
| Compression | No configured workload codec; no measured codec ratio. Allocation is not a compression measurement. |

Native snapshots include verification/background work; Docker sampling uses a
slightly different window. Do not add the two I/O scopes together. Application
RSS/CPU excludes the mount daemons/backends; container/stack metrics include them.

Final reconciliation covered **2,621 ACKs plus one observed unacknowledged
publication**, **687,341,568 logical bytes**, and **7,866 direct replica reads /
2,062,024,704 bytes**. All bodies matched. Thirty ambiguous HTTP paths remained
absent. Totals include the failed larger FUSE run and fault workload, so total
retained FUSE/HTTP histories differ; each timed pair still used identical input.

Both teardown steps passed after exact reconciliation. The HTTP/FUSE application
containers, owned storage/network and both local image tags were removed. Raw
journals, failed evidence and resource samples remain ignored locally.

## Limits and next tests

- Diagnose the recurring FUSE publication stalls with syscall/metadata traces;
  the 1,000-file failure stays failed regardless of the smaller trial’s success.
- Timed loops have exact readback after each write phase and at the end; they do
  not have an independent periodic reader inside the timed write loop. The live
  writer/reader recovery test is separate. These are bounded diagnostics, not
  sustained distributed-performance qualification.
- Small-file/metadata workloads, overwrite/delete/list/append semantics, API auth/TLS,
  one-filer routing, volume/disk loss, etcd quorum loss/partitions, compaction,
  pressure, upgrades and independent restore are unrun for this HTTP interface.
- Native gRPC/S3 and Ceph FUSE variants remain future work. Results here do not
  compare SeaweedFS to Ceph’s differently budgeted, emulated guest deployment.
- The initial controller registered both paired paths before either ran. After
  the FUSE timeout, the unstarted HTTP reservation blocked audit. Its original
  fixture was preserved; missing journal and HTTP 404 on both filer namespaces
  proved no HTTP publication. Only that reservation was retired. The controller
  now registers each path immediately before its own writes. Existing ACKs,
  unfinished intents and observed publications are never removed from audit.

## Upstream basis

- [Native filer API](https://github.com/seaweedfs/seaweedfs/wiki/Filer-Server-API).
- [Pinned upload receipt/fsync implementation](https://github.com/seaweedfs/seaweedfs/blob/4.55/weed/server/filer_server_handlers_write.go).
