# HDFS metadata HA with live file publication

Two NameNodes, three JournalNodes, three ZooKeeper voters, two ZKFC controllers
and three DataNodes. A C++17 `libhdfs` writer publishes files while an independent
reader verifies their bodies. This single-Docker-VM lab tests process failover,
not independent host, disk or availability-zone failures.

## Quick start

1. `make docker-up` builds pinned images, generates lab-only fencing keys and starts the quorums.
2. `make up` initializes only fresh state, bootstraps the standby and waits for native HA/data health.
3. `make test` publishes a 64 MiB fixture and verifies complete bodies and three physical copies.
4. `make down` stops workload actors, verifies an exported witness archive and stops the data plane; volumes and quorums remain.
5. `make docker-down` exports the witness again and removes only this Compose project's containers, volumes and network; images remain cached.

Requires Docker Compose, Make and Python 3. C++/CMake, Hadoop and its supplied JVM
are built inside Docker. There are no host ports or host toolchain, SSH-key,
Docker-socket, device or cloud-credential mounts. Every service uses the owned
internal network. Budget approximately 14 GiB of container memory limits plus
Docker/build headroom. Do not run competing heavy benchmarks alongside this lab.

Run the commands separately. Repeating `make up` preserves acknowledged files;
`make test` refuses to overwrite an existing fixture. Use `make verify` to recheck
it. `docker-down` is destructive after verified witness preservation; it is not a
backup of the HDFS file bodies.

## Files and responsibilities

| Path | Responsibility |
|---|---|
| `files-workload/` | C++17 application, CMake/Ninja and Dockerfile; native `libhdfs`, OpenSSL EVP and JSON libraries |
| `config/` | Namespace, replication, quorum, timeouts and backend contract |
| `docker/` | Checksum-pinned Hadoop runtime and fencing tools |
| `fencing/native-jsch/` | Original native fencer, failed result and reproduction configuration |
| `fencing/openssh/` | Bounded OpenSSH replacement used by the ZKFC images |
| `scripts/` | Setup, fault/recovery controllers, independent replica checks, witness export and resource/report tools |
| `reports/` | Compact measured summaries; ignored `results/` owns raw histories and logs |

These fencing variants share this lab's infrastructure, namespace, keys, witness
and lifecycle. They are alternative policies, not independent examples.

## Workload and correctness

The native C API embeds Hadoop's supplied JVM; no custom Java application or
inspection helper is used. One connection stays alive through failover. Before a
mutation, the writer fsyncs its attempt to an independent volume. It writes a
unique staging file, hsyncs and closes it, atomically publishes by rename, then
fsyncs its ACK. Application retries do not replay ambiguous publications.

A separate reader checks SHA-256 and length against ACKs. Fresh native clients
reconcile all ACKs and ambiguous final paths after recovery. Python's standard
library independently hashes ordered finalized blocks on **each of the three
DataNodes**. Native `fsck` must also report healthy replication. A healthy service
or matching file count cannot override a wrong body.

The seed defaults to 64 MiB; live files are 1 MiB, paced at 200 ms, with a 100 ms
reader pause. Deterministic seed 42193 generates poorly compressible bytes.
This is recovery traffic, not a throughput saturation benchmark. File publication
latency includes write, hsync, close and rename; connection setup is separate.
Native client retries can outlast a single configured RPC timeout; controllers
supply external run deadlines. Timed-out writes remain ambiguous until reconciled.

## Failure and recovery targets

- `make failover`: requires a verified fixture; proves live progress, discovers and SIGSTOPs the active native JVM, then requires promotion and actual PID fencing within 60 seconds. Restarts the fenced daemon as standby, requires more ACKs and verifies every file/copy.
- `make test-production`: serial `verify`, `failover`, `verify`; failures exit nonzero.
- `make verify`: full retained-history and physical replica readback without reseeding.
- `make status`: native deployment and role state.

NameNode containers model separate sshd and Hadoop processes. ZKFCs share their
NameNode's network namespace. The default replacement uses Hadoop's `shell(...)`
hook and maintained OpenSSH, with strict generated host-key pins and a 12-second
transport timeout plus two-second kill grace. It permits only owned names/port,
kills the native listener and requires connection refusal. The host test also
requires the exact paused PID in the proof. No success-only fallback is allowed.

Never manually promote until the old active is fenced and both ZKFCs are stopped.
An interrupted administrative command has an ambiguous outcome: inspect native
roles before retrying. The manual `--forcemanual` command prompts for confirmation;
a closed stdin can loop and generate enormous output. Bound it, explicitly supply
confirmation only after safety checks, and preserve its outcome. Never format a
surviving namespace to make recovery pass.

## Measured results and limitations

Pinned Hadoop **3.5.0**, ZooKeeper server **3.9.7** (bundled Hadoop client 3.8.6),
ARM64 Docker VM with 11 CPUs and approximately 24.4 GiB RAM. See
[status](reports/STATUS.json), [live trial](reports/OPENSSH_FAILOVER.json),
[first observed resources](reports/OPENSSH_RESOURCES.json),
[fresh failover](reports/FRESH_FAILOVER.json), [fresh resources](reports/FRESH_RESOURCES.json) and
[retained storage counters](reports/RETAINED_STORAGE.json).

| Case | Observed outcome |
|---|---|
| Baseline | 67,108,864 bytes, four 16 MiB blocks, three independently matching physical copies; publication 374.142 ms |
| Native Hadoop/JSch fencing | Failed: old PID killed but standby stayed standby beyond 60 s; native thread dump showed an output-drain wait after about 200 s |
| Manual recovery | Both controllers stopped; after Docker restart both NNs were standby, confirmed promotion restored service; all prior ACKs and physical bodies verified |
| First OpenSSH draft | Failed proof gate: immediate probe raced asynchronous socket closure; native retry promoted, but lacked the killed PID in its second proof |
| Bounded OpenSSH trial | Passed: 29.317 s promotion/fencing, old node returned standby; 19 live ACKs, zero ambiguous publications; full retained fixture: 34 files, 37 blocks, 101,711,872 logical bytes, three matching copies |
| Fresh rebuild | `test-production` passed: 29.616 s fencing/promotion, 19 ACKs and 48 successful live reads; 20 files, 23 blocks, 87,031,808 logical bytes, three matching physical copies |
| Fencing negative controls | Unowned hostname and wrong RPC port rejected before SSH |
| Repeated setup | Separate `make up` and `make verify` passed against retained ACK history without reloading |
| Cleanup | Three verified witness exports/owned teardown cycles passed; fresh format/index archival and final five-command lifecycle passed |

These are diagnostics at approved Level 3 depth, not a repeated fair performance
comparison. Successful live publications ranged from 21.936 ms to 30,671.048 ms;
the slow operation includes failover retries. The native fencer failure is bounded
to the observed Hadoop/JSch output path; its precise cause remains unresolved.
OpenSSH's success does not erase that failure or establish production readiness.

The fresh serial production run sampled 2,527,899,648 bytes aggregate charged
memory at peak and 30.011 CPU core-seconds across observed container intervals.
These exclude work outside the sample windows; they are not whole-lifecycle totals.
Resource summaries include sampled CPU utilization/core-seconds, charged memory,
disk read/write bytes and native I/O counts. Short-lived actors and between-sample
peaks can be missed; exit cgroup counters supplement observation. Device I/O latency
is not measured. HDFS stores raw body bytes: body compression ratio is **1.0**;
replication triples payload storage, with checksums/metadata and allocation reported
separately. New clients do not guarantee cold reads; shared caches are not cleared.

The initial loopback HTTP readiness probe was wrong because the NameNode bound its
configured hostname. Retained metadata survived the correction; failed logs remain.
A fresh lifecycle initially refused seeding because a host fixture index had not
been retired after verified teardown. The archival step was fixed and a further
fresh cycle passed; original witnesses and both failed setup/probe attempts remain.
A later Docker outage interrupted manual recovery. Its repetitive confirmation log
was losslessly compressed from 6,049,091,310 to 44,149,698 bytes and verified by
SHA-256 before deleting the original; the archive and receipt remain ignored.

**Future coverage:** JournalNode/ZooKeeper quorum loss, fencing-channel loss,
asymmetric partitions, permanent disk loss/replacement, independent backup/restore,
upgrades, balancing, sustained capacity/pressure and security/credential rotation.
The private network and simple Hadoop authentication do not qualify Kerberos,
ZooKeeper ACL/TLS or production SSH security. Native JSch disables host-key checking.
There is no power-loss durability claim or independent failure-domain proof.

[Hadoop 3.5 HA guide](https://hadoop.apache.org/docs/r3.5.0/hadoop-project-dist/hadoop-hdfs/HDFSHighAvailabilityWithQJM.html)
· [Native fencing source](https://github.com/apache/hadoop/blob/rel/release-3.5.0/hadoop-common-project/hadoop-common/src/main/java/org/apache/hadoop/ha/SshFenceByTcpPort.java)
· [Shell fencing source](https://github.com/apache/hadoop/blob/rel/release-3.5.0/hadoop-common-project/hadoop-common/src/main/java/org/apache/hadoop/ha/ShellCommandFencer.java)
· [Native client API](https://github.com/apache/hadoop/blob/rel/release-3.5.0/hadoop-hdfs-project/hadoop-hdfs-native-client/src/main/native/libhdfs/include/hdfs/hdfs.h)
