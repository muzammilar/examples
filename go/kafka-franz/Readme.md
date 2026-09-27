# Trees - Kafka Sync/Async Producers with a Consumer Group Example (franz-go)

```sh
# Library used
https://github.com/twmb/franz-go

# Alternatives (Sarama has stickyness issues)
https://github.com/segmentio/kafka-go
https://github.com/lovoo/goka
```

A basic example of Kafka topic creation, sync/async producers and a consumer group using the `franz-go` library, with client stats exposed to Prometheus.
It is the `franz-go` equivalent of `kafka-trees` (please see `kafka-trees` for a `sarama` example). The example uses tree names (botanical trees - not software trees) for pub.
See the `trees` directory for code. In order to avoid multiple small modules for this PoC, all of the shared code is in the `common` package.

* `trees/admin` - creates the topics using the `kadm` admin client (skips topics that already exist) and exits.
* `trees/producer` - each worker runs a sync producer (`ProduceSync`) and an async producer (`Produce` with a promise callback) sharing one `kgo.Client`.
* `trees/consumer` - a consumer group member (`kgo.ConsumerGroup`) that marks processed records and auto-commits the marked offsets (at-least-once).
  The partition balancer can be selected with `-balancer` (`range`, `roundrobin`, `sticky`, `cooperative-sticky`).

The Kafka cluster runs in KRaft mode (3 controllers and 3 brokers, no zookeeper). The `admin` service waits for the brokers to be healthy,
creates the `trees` and `test` topics (13 partitions, replication factor 3), and the producers and consumers start once it has completed.

In order to experiment with the consumer group, change the number of replicas for consumer to see the partitions being reassigned.

```sh
# run containers
docker compose up --build --detach

# follow the consumer logs (partition assignment and processed fetches)
docker compose logs --follow consumer

# Multiple Kafka consumers (you can stop individual consumer to see behaviour of others)
docker compose up --build --detach --scale consumer=5

# Shutdown everything (and remove networks and local images). Networks are removed in this.
# This is usually needed to cleanup kafka volumes (for the PoC)
docker compose down --volumes
# `make docker-down` (in `trees`) also removes the locally built franz-tree-* images
# Remove everything (and remove volumes). Networks are not removed here.
docker compose rm --force --stop -v
```

### Running locally

The brokers are exposed on the host at `localhost:29092,localhost:39092,localhost:49092`, so the binaries can also run outside of docker
(override with `KAFKA_BROKERS=...`).

```sh
# start only the kafka cluster
docker compose up --detach kafka-broker-1 kafka-broker-2 kafka-broker-3

cd trees
make build               # build the binaries in trees/bin
make run-admin           # create the topics
make run-producer        # produce to `trees` (metrics on :8081)
make run-consumer        # consume `trees,test` as a group member (metrics on :8082)

# all flags
go run ./producer -help
```

### Tests and Benchmarks

Run from the `trees` directory (`make help` lists all the targets). The unit tests and benchmarks do not need a broker.
The integration tests and the produce benchmarks are behind the `integration` build tag and expect the compose kafka to be up
(they use `KAFKA_BROKERS`, defaulting to the host ports above).

```sh
cd trees
make test                # unit tests: flag/config parsing, partitioner/balancer selection, topic configs, record encoding/decoding
make test-race           # unit tests with the race detector
make bench               # encode/decode benchmarks (BENCH=<regex>, BENCH_TIME=5s, BENCH_COUNT=5)
make bench-cpu           # benchmarks with GOMAXPROCS 1,4,8
make test-all            # vet + test-race + bench

make docker-up           # start the compose stack (detached)
make test-integration    # topic creation, produce + consume with a consumer group, committed offsets
make bench-integration   # sync (ProduceSync) and async (Produce + Flush) produce benchmarks
make docker-down         # stop the stack and remove the volumes and locally built images
```

### Kafka Topic Creation (Kafka CLI)

The topics are created by the `admin` service, but the Kafka CLI can be used as well.

```sh
## Either: create topics in kafka from one host
docker exec --workdir /opt/kafka/bin/ -it kafka-broker-1 sh
./kafka-topics.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --create --topic test-topic
./kafka-console-consumer.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --topic test-topic --from-beginning
./kafka-console-producer.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --topic test-topic
# describe the consumer group (members, lag)
./kafka-consumer-groups.sh --bootstrap-server kafka-broker-1:19092 --describe --group treeconsumer

## Or: create topics in kafka from your machine
./kafka-topics.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --create --topic test-topic2
./kafka-console-producer.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --topic test-topic2
./kafka-console-consumer.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --topic test-topic2 --from-beginning
```

## Kafka

Using the official Apache Kafka docker image [here](https://hub.docker.com/r/apache/kafka).

## Monitoring

`docker compose up` also starts Prometheus and a pre-provisioned Grafana (the datasource and dashboard are provisioned from `grafana/provisioning`).

| Service    | URL                                                                   | Notes                                                       |
|------------|-----------------------------------------------------------------------|-------------------------------------------------------------|
| Grafana    | [http://localhost:13014/d/kafka-franz](http://localhost:13014/d/kafka-franz) | anonymous `Admin`, no login; the dashboard is the home page |
| Prometheus | [http://localhost:19114](http://localhost:19114)                      | host port `19114` (container port `9090`)                   |

The producers and consumers expose the franz-go client metrics (via the [kprom](https://github.com/twmb/franz-go/tree/master/plugin/kprom) plugin) on port `8080` at `/metrics`,
prefixed with `treeproducer_` and `treeconsumer_`. Prometheus scrapes the producers statically and discovers the consumers through the docker DNS
(`dns_sd_configs` on `consumer`), so scaling the consumer group is picked up within ~15s.
Besides the kprom defaults (bytes produced/fetched by topic and node, broker connects/disconnects, read/write bytes and the client buffers),
the example enables the `produce_records_total`/`fetch_records_total` and `*_batches_total` counters and the `request_duration_e2e_seconds` histogram.

The **Kafka franz-go clients** dashboard (`grafana/provisioning/dashboards/kafka-franz.json`, refresh 5s, last 15m) shows:

* Overview: live consumer and producer instances (`count(up{job="treeconsumer"} == 1)`), total produced and fetched records/s.
* Produce: bytes/s and records/s by topic and producer instance, records per batch.
* Consume: fetch bytes/s and records/s by topic and consumer instance, records per batch.
* Buffering (backpressure): records and bytes buffered for produce (producers) and fetched-but-not-polled (consumers).
* Brokers: write/read bytes/s, connects/disconnects and end-to-end request latency (p50/p99) per broker `node_id`.

```sh
# scale the consumer group and watch "Live consumer instances" and the per-instance fetch panels
docker compose up --detach --scale consumer=3 --no-recreate
```

The connect/read/write error counters are only exported by kprom once an error has happened, so they are not on the dashboard.
Kafka broker (JMX) metrics are not collected: the `apache/kafka` image has no Prometheus exporter built in.
Check the details about the Prometheus docker image [here](https://hub.docker.com/r/prom/prometheus) and Grafana [here](https://hub.docker.com/r/grafana/grafana).
