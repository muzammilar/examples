#!/bin/bash
# Kafka topic initialization script
# Creates the user_analytics topic with specified configuration

set -e

echo "Waiting for Kafka to be ready..."
# Wait a bit for Kafka to fully start
sleep 10

echo "Creating Kafka topic: user_analytics"
# Create topic with 1 partition and replication factor 1
# --if-not-exists prevents errors if topic already exists
/opt/kafka/bin/kafka-topics.sh --create \
  --topic user_analytics \
  --bootstrap-server kafka:9092 \
  --partitions 1 \
  --replication-factor 1 \
  --if-not-exists

echo "Listing all Kafka topics:"
/opt/kafka/bin/kafka-topics.sh --list --bootstrap-server kafka:9092

echo "Kafka topic initialization complete"
