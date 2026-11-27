# Doris replicated mutable orders

Three voting frontends and three backends, with three tablet replicas per table.
The workload verifies every body in one million deterministic orders and then
applies a higher sequence revision without increasing the logical row count.

## Quick start

1. `make docker-up` builds and starts six example-owned Doris roles.
2. `make up` waits for native disk-capacity reports and creates the RF3 table.
3. `make test` seeds one million orders and reads every body independently.
4. `make down` removes the example database.
5. `make docker-down` removes all owned roles, volumes, network and built images.

## Data path and operations

`orders-workload/` is a local Go program using the maintained MySQL driver.
`docker/` contains version/digest-pinned service images. `scripts/observe.py`
records CPU, memory and device-byte/I/O counters during a selected Make target.
Native topology and tablet observations live under ignored `results/`.

Use `make backend-restart` after the initial revision-1 baseline. It verifies all
rows, SIGKILLs one BE, verifies while that BE is absent, restarts retained storage
and checks the full result again. This is one retained-backend outage, not permanent
disk loss or an independent restore. Use `make mutate` afterwards to write revision
2 and verify all expected amounts and sequence values. The seed and mutation are
one native bulk SQL statement each, not one million external write acknowledgements.

This lab has no published host ports and uses an internal network. It shares one
physical host/failure domain. Each FE has 0.5 CPU / 2 GiB and a 1 GiB heap; each BE
has 1 CPU / 4 GiB, a 2 GiB native memory limit and one configured native core.
Combined service memory caps are 18 GiB, plus a 256 MiB workload role. Do not run
another heavy lab concurrently. These are lab budgets, not production sizing.

## Qualification

The initial million-row full-body readbacks passed through each native fixed
replica selection (indices 0, 1 and 2). The controller disables query/SQL caches,
missing-version skips and fallback to a different fixed replica. Source confirms
the index sorts replicas by ID for every tablet. Native observations show three
live voting FEs, one leader, three BEs and eight tablets with 24 NORMAL replica
records whose versions agree. The revision-2 update and bounded recovery follow-up also passed. All three
fixed selections returned exactly 1,000,000 rows and 1,599,500,000 cents after
updating every order. The logical row count did not double.

An initial topology assertion used the wrong column name, `LastFailedVersion`;
the released interface returns `LstFailedVersion`. That controller failed after
all three body checks had passed. Its observations are preserved, and only the
corrected topology gate was rerun against the retained dataset.

Live external writers/readers with durable ACK histories, FE leader loss, empty
BE replacement, compaction interruption, scale-out/in, partitions/quorum loss,
backup/restore, operator deployment and matched level-3 comparison remain pending.
No throughput winner is claimed.

[Native FE bootstrap](https://github.com/apache/doris/blob/4.0.8/docker/runtime/fe/resource/init_fe.sh)
· [Sequence columns](https://doris.apache.org/docs/4.x/data-operate/update/sequence-column-manual/)

[Native fixed-replica selection implementation](https://github.com/apache/doris/blob/4.0.8/fe/fe-core/src/main/java/org/apache/doris/planner/OlapScanNode.java).

## Measured recovery and limitations

| Observation | Result |
|---|---|
| Immediate first read after BE crash | **Failed**, native RPC retries still targeted the dead BE |
| First failed read in follow-up | 40.75 seconds |
| Fresh exact read with BE still down | 42.57 seconds after fault; 0 wrong/missing rows |
| Restored fixed-replica reads | All three passed |
| Native table display after update | 10.490 MB across 24 replica records |
| Allocated storage across six role data directories | 286,957,568 bytes |
| Native BE core / memory readback | 1 / 2 GiB on each BE |

[Curated outcomes, CPU/memory, disk-byte rates and I/O counters](reports/INITIAL_RESULTS.json).
The larger directory footprint includes native/internal files, obsolete versions
and temporary data at this observation point. It is not settled compaction usage
or a compression ratio. Native compressed/uncompressed byte accounting is
unavailable. Raw Docker counters cover CPU, memory, disk bytes and network;
final cgroup snapshots supply lifetime I/O-operation counts. Per-phase IOPS,
device latency and complete short-lived actor accounting remain incomplete.

`make backend-restart` remains a strict immediate-read case with a recorded failed
result. `make backend-recovery` is a separate characterization: it permits RPC
failures but requires a fresh exact-body read within a predeclared 90-second
budget while the BE remains stopped, then three-copy verification after restart.
The successful follow-up does not erase the failed first query. These are
quiesced-data checks; live-write recovery and acknowledged-write loss are unrun.
Both fault commands expect revision 1 and should precede `make mutate`.

[Declared contract and reference client configuration](config/contract.json).
