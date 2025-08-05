# Kafka Cluster

```sh
# run containers
docker compose up --build --detach

# Shutdown everything (and remove networks and local images). Networks are removed in this.
# This is usually needed to cleanup kafka volumes (for the PoC)
docker-compose down --volumes
# Use `docker-compose down --rmi all --volumes` with above to images as well
# Remove everything (and remove volumes). Networks are not removed here.
docker-compose rm --force --stop -v

```

### Kafka Publish and subscribe

Topics are already created by one of the containers, so you just need to publish or subscribe to the topics.

```sh
## Either: create topics in kafka from one host
docker exec --workdir /opt/kafka/bin/ -it kafka-broker-1 sh
./kafka-topics.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --create --if-not-exists --topic test-topic  # This is already done by one of the containers
./kafka-console-consumer.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --topic test-topic --from-beginning
./kafka-console-producer.sh --bootstrap-server kafka-broker-1:19092,kafka-broker-2:19092,kafka-broker-3:19092 --topic test-topic

## Or: create topics in kafka from your machine
./kafka-topics.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --create --if-not-exists --topic test-topic2 # This is already done by one of the containers
./kafka-console-producer.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --topic test-topic2
./kafka-console-consumer.sh --bootstrap-server localhost:29092,localhost:39092,localhost:49092 --topic test-topic2 --from-beginning


```

## Kafka

Using the official Apache Kafka docker image [here](https://hub.docker.com/r/apache/kafka).
