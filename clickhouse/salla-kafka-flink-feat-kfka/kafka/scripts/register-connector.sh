#!/bin/sh

set -e

CONNECT_HOST="connect"
CONNECT_PORT=8083
CONNECT_URL="http://${CONNECT_HOST}:${CONNECT_PORT}"

echo "Waiting for Kafka Connect to start listening on ${CONNECT_HOST}:${CONNECT_PORT}..."

# Wait until Kafka Connect REST API is available
while ! curl -s "${CONNECT_URL}/connectors"; do
  sleep 5
  echo "Waiting for Kafka Connect to be available..."
done

echo "Kafka Connect is available. Registering the Debezium connector..."

curl -X POST \
  -H "Content-Type: application/json" \
  --data @/debezium-mysql-connector.json \
  "${CONNECT_URL}/connectors" || echo "Connector might already be registered."

echo "Connector registration script completed."
