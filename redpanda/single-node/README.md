# Redpanda — single node

One Redpanda broker in `dev-container` mode with Redpanda Console, on Docker Compose. Walks
through topics and consumer groups with `rpk`, the built-in Schema Registry and HTTP Proxy, and
Console's API; `make benchmark` times one `rpk` producer and consumer.

## Quick start

```bash
make up         # broker + Console, waits until both are healthy, prints health and license state
make test       # scripts/01-04 (below); exits non-zero on the first failure
make benchmark  # rpk produce + consume of 1M x 1000-byte records, broker capped at 2 CPUs / 3 GB
make status     # containers, rpk cluster health, rpk cluster license info
make cli        # bash in the broker container (rpk is preconfigured for it)
make console    # prints the Console URL, http://localhost:8080
make down       # remove the containers, the data volume and the network
```

## Setup

| service | image | host port (variable) | role |
|---|---|---|---|
| `redpanda-sn-0` | `docker.redpanda.com/redpandadata/redpanda:v26.2.3` | `19092` Kafka (`REDPANDA_KAFKA_PORT`), `18081` Schema Registry, `18082` HTTP Proxy, `19644` Admin API | broker, `--smp 2 --memory 2G` (`REDPANDA_SMP`, `REDPANDA_MEMORY`) |
| `redpanda-sn-console` | `docker.redpanda.com/redpandadata/console:v3.12.0` | `8080` (`CONSOLE_PORT`) | web UI; reads Kafka, Schema Registry and Admin API |

- All ports bind `127.0.0.1`; no authentication.
- Two listeners per API, as in the [quickstart](https://docs.redpanda.com/current/get-started/quick-start/):
  `internal` (`redpanda:9092`, for containers) and `external` (`localhost:19092`, for the host).
- `--mode dev-container` bundles `--overprovisioned` (no CPU pinning or busy polling),
  `--reserve-memory 0M`, `--check=false`, `--unsafe-bypass-fsync` and cluster properties such as
  `write_caching_default: true` and `storage_min_free_bytes: 10485760` (`rpk redpanda start --mode help`).
  Writes are acknowledged before they reach disk, so the benchmark below is not a durable-write
  number; it is "only for development and testing".
- A new cluster starts a 30-day built-in Enterprise trial (`rpk cluster license info`:
  `Type: free_trial`, `Expires: Nov 3 2026` on a cluster created 2026-10-04). During the trial the
  cluster reports `Enterprise features in use: [core_balancing_continuous partition_auto_balancing_continuous]`:
  those are default-on until the trial ends. Nothing in this example needs a license.

## What `make test` does

| script | runs in | what |
|---|---|---|
| [`01-topics.sh`](scripts/01-topics.sh) | broker | `rpk topic create` (3 partitions), produce 5 keyed records (same key → same partition), consume, group `billing` reads 3 and commits; checks the group's total lag is 2 |
| [`02-schema-registry.sh`](scripts/02-schema-registry.sh) | broker | registers an Avro schema for `payments-value`, `rpk topic produce --schema-id=topic` encodes JSON as Avro, `--use-schema-registry=value` decodes it; `BACKWARD` compatibility accepts v2 (new field with a default) and rejects v3 (new required field, `READER_FIELD_MISSING_DEFAULT_VALUE`) |
| [`03-http-proxy.sh`](scripts/03-http-proxy.sh) | broker | HTTP Proxy (pandaproxy): `POST /topics/clicks` with 3 JSON records, consumer instance in group `web`, subscribe, `GET …/records`; checks 3 records came back |
| [`04-console.sh`](scripts/04-console.sh) | console | Console's REST API (`/api/topics`, `/api/schema-registry/subjects`, `/api/consumer-groups`) lists what 01-03 created |

Schema Registry and HTTP Proxy are part of the broker binary (no separate service); both are
Kafka-compatible APIs (Confluent Schema Registry and REST Proxy v2).

## Benchmark

`make benchmark` runs [`bench/run.sh`](bench/run.sh) in a separate container from the same image
(rpk only): writes `RECORDS` lines of `RECORD_SIZE` bytes to a file, `rpk topic produce`
(`--acks -1`, no compression) into a `PARTITIONS`-partition topic, then `rpk topic consume` them
back from offset 0 and checks the count. The broker is capped with
[`bench/limits.sh`](bench/limits.sh) (`BENCH_CPUS=2`, `BENCH_MEM=3g`); the rpk container has
`cpus: 2`. Raw output in `results/` (gitignored).

rpk has no load-generator mode: this is one franz-go client fed from a pipe, i.e. what a shell
pipeline gets, not the broker's limit. For many concurrent clients and latency percentiles see
the workload example.

| date | setup | records | produce | consume |
|---|---|---|---|---|
| 2026-10-04 | 1 broker `--smp 2 --memory 2G`, capped 2 CPUs / 3 GB; rpk 2 CPUs; 6 partitions, acks=all, no compression | 1,000,000 x 1000 B (954 MiB) | 152,161 records/s, 145.1 MiB/s (6.57 s) | 881,057 records/s, 840.2 MiB/s (1.14 s) |

Apple M4 Pro, Docker VM aarch64 (Docker 29.5.3, 11 CPUs, 24.4 GB), native arm64 image, Redpanda
v26.2.3. One run; the Docker VM was shared with another project's benchmark at the time.

## Known issues

- `rpk registry schema check-compatibility` needs `--schema-version` (`Error: required flag(s)
  "schema-version" not set`); pass `--schema-version latest`.

## Links

- [Quickstart (Docker)](https://docs.redpanda.com/current/get-started/quick-start/)
- [rpk reference](https://docs.redpanda.com/current/reference/rpk/)
- [Schema Registry](https://docs.redpanda.com/current/manage/schema-reg/schema-reg-overview/), [HTTP Proxy](https://docs.redpanda.com/current/develop/http-proxy/)
- [Redpanda Console](https://docs.redpanda.com/current/console/)
