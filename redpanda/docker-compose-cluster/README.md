# Redpanda — 3-broker cluster with Docker Compose

Three Redpanda brokers and Redpanda Console on Docker Compose. Topics use RF=3 (one Raft group
per partition). `make failover` stops a partition leader under acks=all load and checks that no
acknowledged record is lost; `make scale-demo` grows the cluster to 5 brokers and back to 3
(decommission) under the same load.

## Quick start

```bash
make up        # 3 brokers + Console, waits until rpk cluster health says healthy
make test      # scripts/01-replication.sh: RF=3 topic, write via one broker / read via another, Schema Registry on every broker
make failover  # stop the leader of partition 0 under load, restart it, read everything back (below)
make scale-out # 3 -> 5: start redpanda-3/4, wait until the balancer has moved replicas onto them
make scale-in  # 5 -> 3: rpk cluster brokers decommission both, wait, remove them and their volumes
make scale-demo # scale-out + scale-in with the load client running; per-phase table (below)
make load      # the load client alone (RATE, DURATION, PARTITIONS, RECORD_SIZE, METADATA_MIN_AGE)
make status    # containers, brokers (rpk cluster info -b), rpk cluster health
make cli       # bash in redpanda-cc-0 (rpk preconfigured)
make down      # remove containers, volumes, network and the built load image
```

## Setup

| service | image | host port (variable) | role |
|---|---|---|---|
| `redpanda-cc-0` | `docker.redpanda.com/redpandadata/redpanda:v26.2.3` | `19192` Kafka (`REDPANDA_KAFKA_PORT_0`), `19744` Admin | starts the cluster (no `--seeds`) |
| `redpanda-cc-1` | same | `19193`, `19745` | `--seeds redpanda-0:33145` |
| `redpanda-cc-2` | same | `19194`, `19746` | `--seeds redpanda-0:33145` |
| `redpanda-cc-3`, `redpanda-cc-4` | same, profile `scale` | – | added by `make scale-out`, removed by `make scale-in` |
| `redpanda-cc-console` | `docker.redpanda.com/redpandadata/console:v3.12.0` | `8180` (`CONSOLE_PORT`) | web UI over all three brokers |
| `load` (profile `tools`) | built from [`client/`](client) | – | Go, [franz-go](https://github.com/twmb/franz-go) v1.22.1 |

- Each broker: `--mode dev-container --smp 1 --memory 1536M` (`REDPANDA_SMP`, `REDPANDA_MEMORY`).
  Ports bind `127.0.0.1`; no authentication.
- `dev-container` mode bundles `--unsafe-bypass-fsync` and `write_caching_default: true`
  (`rpk redpanda start --mode help`): acks=all returns once a majority has the batch in memory.
  The failover test survives that because `docker stop` does not lose the page cache; a power
  loss on all three could. [`../streaming-latency`](../streaming-latency) runs without it.
- Seed layout as in the [quickstart](https://docs.redpanda.com/current/get-started/quick-start/).
  Node IDs are assigned at join time and need not match the container number (in the runs
  below `redpanda-2` got ID 1), so scripts map IDs to hosts with `rpk cluster info -b`.
- Schema Registry and HTTP Proxy run in every broker; schemas live in the `_schemas` topic, so
  any broker serves them.

## Failover

[`scripts/failover.sh`](scripts/failover.sh):

1. starts the load client ([`client/main.go`](client/main.go)): `RATE=5000` records/s of 512 B
   for 60 s into topic `failover` (6 partitions, RF 3), acks=all, idempotent producer, delivery
   timeout 30 s; a consumer group reads along. One line per second: acked, failed, consumed, ack
   latency p50/p99/max.
2. after 15 s, finds the leader of partition 0 and `docker stop`s that broker;
3. 20 s later `docker start`s it and times how long until `rpk cluster health` is healthy with 0
   under-replicated partitions;
4. when the load ends, the client reads the topic back from offset 0 and checks every
   acknowledged record is there exactly once (exit 1 otherwise).

`METADATA_MIN_AGE` is franz-go's `MetadataMinAge`: how soon the client may refresh metadata
after a `NOT_LEADER_FOR_PARTITION` error (default 5 s).

### Results

2026-10-04, Apple M4 Pro, Docker VM aarch64 (Docker 29.5.3, 11 CPUs, 24.4 GB), Redpanda v26.2.3,
no CPU caps; one run each; the Docker VM was shared with other projects.

| run | stopped | acked | failed | lost | duplicates | slowest ack | acks > 1 s | rejoin (healthy, 0 under-replicated) |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `make failover` (`METADATA_MIN_AGE=5s`) | `redpanda-cc-0` (ID 0) | 299,950 | 0 | 0 | 0 | 9,970.9 ms | 516 | 4.5 s |
| `make failover METADATA_MIN_AGE=500ms` | `redpanda-cc-1` (ID 2) | 299,950 | 0 | 0 | 0 | 5,537.6 ms | 432 | 15.5 s |

- Writes never stopped as a whole: partitions led by the other two brokers kept acking at
  5,000/s. Records for the stopped broker's partitions waited for a new leader; franz-go retried
  them (no `failed` record), and the read-back found every acked record once.
- Server side, the new leaders took ~3-5 s. Broker logs in the second run: `starting pre-vote
  leader election … leadership transfer: false` at 17:48:34.4, `became the leader term: 7` at
  17:48:37.4 and 17:48:38.0. A plain `docker stop` does not hand leadership over first.
- The client adds up to `MetadataMinAge` on top: worst ack 10.0 s with the 5 s default, 5.5 s
  with 500 ms.
- Ack latency otherwise: p50 0.3-0.6 ms, p99 mostly under 5 ms at 5,000 records/s.

Not run here: `rpk cluster maintenance enable <id>` before a restart drains leadership from the
broker first ([maintenance mode](https://docs.redpanda.com/current/manage/node-management/));
that is the documented way to restart a broker without the election pause.

## Scaling: 3 → 5 → 3 brokers

[`scripts/scale.sh`](scripts/scale.sh):

| step | commands | what happens |
|---|---|---|
| `out` | `rpk cluster config set partition_autobalancing_mode node_add`; `rpk cluster config set partition_autobalancing_max_disk_usage_percent 99`; `docker compose --profile scale up --wait redpanda-3 redpanda-4` | the brokers join with new IDs; the balancer moves replicas onto them (`node_add`: rebalance when a broker is added) |
| `in` | `rpk cluster brokers decommission <id>` for both | their replicas move to the remaining brokers; the brokers leave the membership; then the containers and volumes are removed (a decommissioned broker cannot rejoin with its old data) |

[`scripts/scale-demo.sh`](scripts/scale-demo.sh) runs the load client (2,000 x 512 B records/s,
topic `scale` with 24 partitions, RF 3, acks=all, idempotent) through: baseline 45 s,
scale-out, 30 s, scale-in, 30 s; then the read-back check.

2026-10-04, same machine and versions as above, one run, no CPU caps, shared Docker VM:

| phase | secs | acked/s | consumed/s | worst 1-s p99 ack | slowest ack | failed |
|---|---:|---:|---:|---:|---:|---:|
| baseline (3 brokers) | 44 | 1,999 | 1,999 | 338.6 ms | 338.7 ms | 0 |
| scale-out | 81 | 2,000 | 2,000 | 4,861.4 ms | 4,868.8 ms | 0 |
| 5 brokers | 30 | 2,000 | 2,000 | 12.4 ms | 12.4 ms | 0 |
| scale-in (decommission 2) | 17 | 2,000 | 2,000 | 5,213.5 ms | 5,224.0 ms | 0 |
| 3 brokers | 30 | 2,000 | 2,000 | 21.7 ms | 21.8 ms | 0 |

- Scale-out: both brokers joined 7 s after `up`; the balancer reported `starting` for ~38 s, then
  moved 29 of 72 replicas of `scale` (24/24/24 → 14/15/14/15/14 per broker ID) and reported
  `ready` 81 s after the start.
- Scale-in: both decommissions finished in 16 s (replicas back to 24/24/24); ~165 MiB in the
  topic at that point.
- Read-back: 405,300 acked, 405,300 in the topic, 0 missing, 0 duplicates; 1,310 acks (0.3%)
  took over 1 s. The ~5 s stalls are partitions whose leader moved, plus franz-go's 5 s
  `MetadataMinAge` (see [Failover](#failover)).
- Without the disk setting, nothing moved: the Docker VM's disk was 95% full (shared with
  other projects), above the balancer's 80% limit, and the controller logged `No nodes are
  available to perform allocation after hard constraints were solved` for every replica.
- `rpk redpanda admin brokers decommission` still works but prints `Command "decommission" is
  deprecated, use "rpk cluster brokers decommission" instead`.

## Known issues

- `rpk cluster health` exits with status 10 when the cluster is unhealthy (e.g. a broker down);
  under `set -o pipefail` that aborts a script. `failover.sh` tolerates it.
- Partition balancer hard constraint: no replica goes to a broker whose disk is over
  `partition_autobalancing_max_disk_usage_percent` (80). On Docker Desktop every broker
  reports the shared VM disk; `scale.sh` raises it to 99.
- During the 30-day trial the default `partition_autobalancing_mode` is `continuous`
  (Enterprise); `scale.sh` sets `node_add`, the Community behaviour, so the run matches a
  cluster without a license.
- `rpk redpanda admin brokers list` prints `Command "list" is deprecated, use "rpk cluster info
  -b --detailed" instead`.
- Running `make failover` while a broker from a previous run is still down stops a second broker
  and leaves partitions without a quorum; the script now refuses to start unless the cluster is
  healthy.

## Links

- [Quickstart (3 brokers)](https://docs.redpanda.com/current/get-started/quick-start/)
- [Raft in Redpanda](https://docs.redpanda.com/current/get-started/architecture/)
- [Decommission brokers](https://docs.redpanda.com/current/manage/cluster-maintenance/decommission-brokers/), [cluster balancing](https://docs.redpanda.com/current/manage/cluster-maintenance/cluster-balancing/)
- [franz-go](https://github.com/twmb/franz-go)
