# Trees - Kafka Sync/Async Producers with a Consumer Group Example

```sh
# Alternative Libraries
https://github.com/twmb/franz-go # relatively stable, powerful
https://github.com/segmentio/kafka-go
https://github.com/lovoo/goka
```


A basic example of a Kafka consumer group (and producers) using Shopify/sarama library with stats exposed to Prometheus. The example uses tree names (botanical trees - not softare trees) for pub.
See the `trees` directory for code. In order to avoid multiple small modules for this PoC, all of the shared code between consumer and producer is in the `common` module

In order to experiment with the consumer group, change the number of replicas for consumer to see session reshuffle.

```sh
# run containers
docker-compose up --build --detach

# Multiple Kafka consumers (you can stop individual consumer to see behaviour of others)
docker-compose up --build --detach --scale consumer=5

# Shutdown everything (and remove networks and local images). Networks are removed in this.
# This is usually needed to cleanup kafka volumes (for the PoC)
docker-compose down --volumes
# Use `docker-compose down --rmi all --volumes` with above to images as well
# Remove everything (and remove volumes). Networks are not removed here.
docker-compose rm --force --stop -v

```

## Kafka

Check the details about the Kafka docker image [here](https://github.com/wurstmeister/kafka-docker).


## Prometheus

Prometheus is available by default on port `9090`. Check the details about Prometheus docker [here](https://hub.docker.com/r/prom/prometheus).
