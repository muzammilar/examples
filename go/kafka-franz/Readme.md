# Trees - Kafka Sync/Async Producers with a Consumer Group Example

```sh
# Library used
https://github.com/twmb/franz-go

# Alternatives (Sarama has stickyness issues)
https://github.com/segmentio/kafka-go
https://github.com/lovoo/goka
```

Please see `kafka-trees` for a `sarama` example.

In order to experiment with the consumer group, change the number of replicas for consumer to see session reshuffle.

```sh
# run containers
docker compose up --build --detach

# Multiple Kafka consumers (you can stop individual consumer to see behaviour of others)
docker-compose up --build --detach --scale consumer=5

# Shutdown everything (and remove networks and local images). Networks are removed in this.
# This is usually needed to cleanup kafka volumes (for the PoC)
docker-compose down --volumes
# Use `docker-compose down --rmi all --volumes` with above to images as well
# Remove everything (and remove volumes). Networks are not removed here.
docker-compose rm --force --stop -v

```

### Kafka Topic Creation

```sh
## Either: create topics in kafka from one host
docker exec --workdir /opt/kafka/bin/ -it kafka-broker-1 sh
./kafka-topics.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --create --topic test-topic
./kafka-console-consumer.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --topic test-topic --from-beginning
./kafka-console-producer.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --topic test-topic

## Or: create topics in kafka from your machine
./kafka-topics.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --create --topic test-topic2
./kafka-console-producer.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --topic test-topic2
./kafka-console-consumer.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --topic test-topic2 --from-beginning


```

## Kafka

Using the official Apache Kafka docker image [here](https://hub.docker.com/r/apache/kafka).


## Prometheus

Prometheus is available by default on port `9090`. Check the details about Prometheus docker [here](https://hub.docker.com/r/prom/prometheus).
