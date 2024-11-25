#!/bin/bash

set -xe

# Start the ZooKeeper service
# Note: Soon, ZooKeeper will no longer be required by Apache Kafka.
nohup "$KAFKA_HOME"/bin/zookeeper-server-start.sh -daemon "$KAFKA_HOME"/config/zookeeper.properties

# Start the Kafka broker service
nohup "$KAFKA_HOME"/bin/kafka-server-start.sh -daemon "$KAFKA_HOME"/config/server.properties

# Create the kafka topics
"$KAFKA_HOME"/bin/kafka-topics.sh --create --partitions 7 --replication-factor 1 --topic fake-faketopicer --bootstrap-server localhost:9092

# Check if the topics exists
"$KAFKA_HOME"/bin/kafka-topics.sh --describe --topic .*faketopic --bootstrap-server localhost:9092
