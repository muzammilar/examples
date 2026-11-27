# Hadoop pseudo-distributed HDFS

One NameNode and one DataNode, RF1, from the checksum-pinned Hadoop 3.5.0
distribution on native ARM64 or AMD64. They are separate containers on one host.

## Quick start

1. `make docker-up` builds and starts both owned HDFS roles.
2. `make up` waits for native DataNode registration and creates the lab directory.
3. `make test` uploads and SHA-256 verifies a deterministic 64 MiB file.
4. `make down` removes only the lab's HDFS namespace.
5. `make docker-down` removes the owned roles, volumes, network and image.

## Status

The initial file upload/readback, native fsck and retained DataNode crash passed
on 2026-11-26. The independent physical block hash also matched the client hash.

| Observation | Measured value |
|---|---|
| Complete input/readback | 67,108,864 bytes |
| SHA-256 | `657bedfeae9a069554e7aee77d25268c194dee39bbb080f3260e58aaf383bd46` |
| Wrong bytes / native fsck verdict | 0 / HEALTHY |
| Data directory after baseline | 67,633,709 bytes |
| NameNode directory after baseline | 1,049,269 bytes |
| Payload encoding | Uncompressed opaque binary |
| Data copies / observed crash exit | 1 / 137 |

Native cgroup CPU, memory and device read/write bytes and I/O-operation counts
are saved in the [report](reports/INITIAL_RESULTS.json). Final DataNode counters
start after its replacement and exclude the initial upload. The VM page cache
remains shared across process restarts. Device latency and complete all-actor
resource timelines remain incomplete.

Run `python3 scripts/observe.py datanode-restart` after the baseline to verify
the retained file following a DataNode crash. The NameNode remains running.
This RF1 baseline has no metadata or data HA.
No YARN scheduler is deployed. Every role has 1 CPU and 1 GiB limits.

Simple authentication and an internal network are local lab settings. Production
authentication/authorization needs a separate explicitly configured profile.
NameNode bootstrap refuses to format an unexpected nonempty metadata directory.
Run `make down` and a separate `make up` before repeating the file test.

## Future work

- [ ] Complete all-actor phase resources and add file publication cases.
- [ ] Add replicated DataNodes, native block-placement and empty-disk rebuild checks.
- [ ] Add QJM/ZooKeeper metadata failover, private kind deployment and Spark/YARN execution.

[Single-node upstream guide](https://hadoop.apache.org/docs/current/hadoop-project-dist/hadoop-common/SingleCluster.html)
· [HDFS HA](https://hadoop.apache.org/docs/current/hadoop-project-dist/hadoop-hdfs/HDFSHighAvailabilityWithQJM.html)

[Declared baseline contract and client settings](config/contract.json).
