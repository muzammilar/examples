# Redpanda — low-latency streaming vs Apache Kafka

The Kafka-API workload Redpanda is built for: acks=all producers and live consumers, measured
end to end. The same client, [`go/kafka-franz/trees/bench`](../../go/kafka-franz/trees/bench)
(Go, franz-go), runs against a 3-broker Redpanda cluster and against the repo's Kafka cluster
from [`go/kafka-franz`](../../go/kafka-franz), with the same caps per broker.

## Quick start

```bash
make compare                   # all three runs below, one cluster at a time, each removed afterwards
make up && make run            # Redpanda only (fsync before ack, the default); make redpanda-stop
make up run WRITE_CACHING=true # Redpanda with write caching (ack from memory, like Kafka)
make kafka-up && make kafka-run   # go/kafka-franz's Kafka, brokers capped the same; make kafka-stop
make status / make cli / make down
```

`make down` removes the Redpanda containers and volumes, the go/kafka-franz containers this
example started (`kafka-broker-1..3`, `kafka-controller-1..3`) with their network, and the bench
image. If you run go/kafka-franz's own stack at the same time, `kafka-stop` stops its brokers too.

## Setup

| | Redpanda | Kafka |
|---|---|---|
| compose | [`docker-compose.yml`](docker-compose.yml) | [`go/kafka-franz/docker-compose.yml`](../../go/kafka-franz/docker-compose.yml) (`docker-kafka*.yml`) |
| image | `docker.redpanda.com/redpandadata/redpanda:v26.2.3` | `apache/kafka:4.3.1` |
| nodes | 3 brokers (Raft controller inside) | 3 brokers + 3 KRaft controllers |
| caps per broker | 2 CPUs, 3 GB (`cpus`, `mem_limit`) | 2 CPUs, 3 GB (`docker update` after start); controllers uncapped |
| process | `--smp 2 --memory 2G --overprovisioned --reserve-memory 0M --check=false` | JVM heap 1 GB (image default) |
| durability on ack (acks=all) | majority of replicas fsynced (default); `WRITE_CACHING=true`: majority has it in memory | all in-sync replicas have it in the page cache, no fsync (default) |
| bench client | `franz-tree-bench` image built from `go/kafka-franz` (`--target bench`), 4 CPUs, on the cluster's network | same |

- Not `--mode dev-container`: that adds `--unsafe-bypass-fsync` and turns on write caching.
- [`redpanda-bootstrap.yaml`](redpanda-bootstrap.yaml): `storage_min_free_bytes` 1 GiB (default
  5 GiB), `segment_fallocation_step` 4 KiB (default 32 MiB per partition replica),
  `transaction_coordinator_partitions` 4 (default 50). With the defaults the transaction phase
  preallocated enough to fill the shared Docker disk and the brokers stopped (see Known issues).
- `make up` first raises `fs.aio-max-nr` to 1,048,576 (privileged one-shot, `aio-max-nr`
  target): Seastar asks for 10,000 AIO events per shard. On Docker Desktop this changes the
  whole Docker VM until Docker restarts; on Linux, the host.

## What `make run` / `make kafka-run` do

Two bench runs per system (`LATENCY_ARGS`, `MAX_ARGS`); each re-creates topic `bench`
(12 partitions, RF 3, `min.insync.replicas=2`), waits for partition leaders, writes one untimed
record per partition, and uses 1 KiB records with 10,000 keys, acks=all, idempotent producer,
linger 0, no compression.

| run | what |
|---|---|
| latency (`-rate 10000 -duration 30s -txns 100`) | 10,000 records/s for 30 s; a consumer reads along; ack latency (send → ack) and end-to-end latency (send → consumed, same host clock), first 2 s not timed. Then 100 transactions of 1,000 records, every 5th aborted |
| max (`-rate 0 -duration 5s -warmup 1s`) | one client producing as fast as it can for 5 s |
| checks (both) | read the topic back from offset 0: every acknowledged record exactly once (count, missing, duplicates, CRC32 sum); `read_committed` returns exactly the committed transactional records, `read_uncommitted` all of them. Exit 1 otherwise |

## Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (Docker 29.5.3, 11 CPUs, 24.4 GB), native arm64
images, caps as above, one run each, one system at a time.

Fixed rate, 10,000 x 1 KiB records/s (9.8 MiB/s), 280,000 timed records each:

| system | ack p50 / p99 / p99.9 / max ms | end-to-end p50 / p99 / p99.9 / max ms | txn begin→commit p50 / p99 ms |
|---|---|---|---|
| Redpanda v26.2.3, fsync (default) | 0.84 / 8.32 / 67.58 / 138.09 | 0.86 / 8.07 / 62.94 / 140.79 | 10.26 / 225.45 |
| Redpanda v26.2.3, write caching | 0.51 / 4.12 / 36.11 / 84.92 | 0.56 / 3.73 / 33.20 / 88.12 | 10.21 / 223.31 |
| Kafka 4.3.1 (no fsync) | 0.62 / 39.13 / 69.76 / 90.62 | 0.74 / 39.45 / 70.19 / 92.05 | 75.37 / 531.16 |

Max rate, one client, 5 s:

| system | acked records/s | MiB/s | end-to-end p50 / p99 ms (queueing) |
|---|---:|---:|---|
| Redpanda, fsync | 196,463 | 191.9 | 138.57 / 749.50 |
| Redpanda, write caching | 154,303 | 150.7 | 209.74 / 1,147.40 |
| Kafka 4.3.1 | 311,192 | 303.9 | 97.13 / 1,369.65 |

Correctness, every run: 0 failed, 0 missing, 0 duplicates, checksum equal (299,990 records per
latency run, 829,296-1,970,185 per max run); transactions: 80,000 committed and 20,000 aborted,
`read_committed` returned exactly the 80,000 (checksum equal), `read_uncommitted` 100,000.

- At a fixed 10k/s, Redpanda's p99 was 4-10x lower than Kafka's (8 ms with fsync, 4 ms
  without, vs 39 ms); p50 was within 0.3 ms for all three. p99.9 was similar (63-70 ms) except with write caching (33 ms);
  the single worst record was slowest on Redpanda with fsync (141 ms vs 92 ms).
- Transactions commit ~7x faster on Redpanda (p50 10 ms vs 75 ms).
- Kafka produced the most at max rate. Write caching was slower than fsync at max rate in this
  single 5 s run; treat the max-rate row as noise-prone (one run, one client with 4 CPUs,
  which may itself be the limit).
- Kafka's three controllers ran uncapped on top of the brokers' 6 CPUs; Redpanda's controller
  shares the brokers' caps.
- All containers share one Docker VM and one disk: this is a laptop comparison, not a
  capacity number.

## Design notes

- **Thread-per-core (Seastar).** Each shard owns its partitions, memory and I/O queue, with
  no shared locks; the log is written with direct I/O and Redpanda's own cache instead of the
  OS page cache. That is the design aimed at a short tail at moderate load.
- **Raft per partition, fsync by default.** acks=all means a majority has fsynced; Kafka's
  acks=all means all in-sync replicas have the data in memory, and Kafka relies on replication
  plus log recovery instead of fsync. `write_caching` gives Redpanda the Kafka-style contract
  and lowered p99 from 8.3 to 4.1 ms here.
- **No ZooKeeper/KRaft quorum to run**, and Schema Registry/HTTP Proxy are in the broker.
- **Independent results differ by workload.** Jack Vanlightly's tests
  ([Kafka vs Redpanda performance: do the claims add up?](https://jack-vanlightly.com/blog/2023/5/15/kafka-vs-redpanda-performance-do-the-claims-add-up),
  2023, three i3en.6xlarge): Redpanda did well in the vendor's low-producer-count benchmark,
  but with 50 producers instead of 4, with record keys (Kafka reached 500 MB/s, Redpanda
  topped out at 330 MB/s), in 24-36 h runs (Redpanda latency spikes after ~12 h as the NVMe
  drives filled) and when draining backlogs, Kafka did better; he also showed the original
  Kafka setup fsynced every message (`log.flush.interval.messages=1`), which is not Kafka's
  default. This example is a single short run with 1 producer: it covers the low-latency case,
  not those.

## Known issues

- With the default `segment_fallocation_step` (32 MiB) and `transaction_coordinator_partitions`
  (50), the first transactional produce created 150 partition replicas of `kafka_internal/tx`,
  the Docker disk went to 0 bytes free and two brokers exited (code 133, `vassert`). Fixed by
  the bootstrap file above.
- `Could not setup Async I/O: unknown error. The required nr_events 10000 exceeds the capacity
  in /proc/sys/fs/aio-max-nr 65536` when a second cluster started while the first was still
  being torn down: the default `fs.aio-max-nr` is shared by the whole Docker VM. `make up`
  raises it.
- franz-go keeps up to 5 produce requests in flight per broker with idempotence and refuses
  `MaxProduceRequestsInflightPerBroker` (`invalid usage of MaxProduceRequestsInflightPerBroker
  with idempotency enabled`).

## Links

- [Topic configuration (write caching)](https://docs.redpanda.com/current/develop/manage-topics/config-topics/), [cluster properties](https://docs.redpanda.com/current/reference/properties/cluster-properties/)
- [go/kafka-franz benchmark](../../go/kafka-franz/Readme.md#benchmark-kafka-and-redpanda)
- [Jack Vanlightly: Kafka vs Redpanda performance](https://jack-vanlightly.com/blog/2023/5/15/kafka-vs-redpanda-performance-do-the-claims-add-up)
