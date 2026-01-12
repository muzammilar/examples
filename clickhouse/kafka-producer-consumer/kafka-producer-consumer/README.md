# ClickHouse - Kafka and MySQL Named Collection Example

## Testing

```bash
# Start all services (MySQL, Kafka, and ClickHouse)
make up

# Query the ClickHouse dictionary and view results
make results-dict

# Query the Kafka analytics consumer and view results
make results-analytics

# Stop all services and remove volumes
make down
```

## Debugging

```bash
# Check Kafka logs to make sure a quorum is reached

# Check if Kafka topic exists
docker exec kafka /opt/kafka/bin/kafka-topics.sh --list --bootstrap-server localhost:9092

# Check if messages are in Kafka topic (first 5 messages)
docker exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic user_analytics --from-beginning --max-messages 5 --timeout-ms 5000

# Read directly from ClickHouse Kafka producer table
docker exec clickhouse clickhouse-client --multiquery --query "SET stream_like_engine_allow_direct_select=1; SELECT * FROM production.kafka_user_analytics_producer LIMIT 5"

# Read directly from ClickHouse Kafka consumer table
#docker exec clickhouse clickhouse-client --multiquery --query "SELECT * FROM consumption.kafka_user_analytics_consumer LIMIT 5"

# Check staging table (intermediate storage before Kafka)
docker exec clickhouse clickhouse-client --query "SELECT * FROM production.user_analytics_staging ORDER BY generated_at DESC LIMIT 5"

# Check ClickHouse error logs for Kafka-related issues
docker exec clickhouse tail -f -n 50 /var/log/clickhouse-server/clickhouse-server.err.log

# Check ClickHouse logs for kafka
docker exec clickhouse tail -f -n 500 /var/log/clickhouse-server/clickhouse-server.log | grep StorageKafka
```

### Sample Insert Statements

```sql
INSERT INTO production.kafka_user_analytics_producer
(generated_at, email, email_domain, full_name, days_since_created, event_timestamp, event_count)
VALUES
(now(), 'alice@example.com', 'example.com', 'Alice Smith', 45, now() - INTERVAL 1 HOUR, 25),
(now(), 'bob@test.com', 'test.com', 'Bob Johnson', 15, now() - INTERVAL 3 HOUR, 38),
(now(), 'charlie@demo.com', 'demo.com', 'Charlie Brown', 60, now() - INTERVAL 5 HOUR, 12);

INSERT INTO production.kafka_user_analytics_producer
(generated_at, email, email_domain, full_name, days_since_created, event_timestamp, event_count)
VALUES
(
    now(),                           -- generated_at
    'user@example.com',              -- email
    'example.com',                   -- email_domain
    'John Doe',                      -- full_name
    30,                              -- days_since_created
    now() - INTERVAL 2 HOUR,         -- event_timestamp
    42                               -- event_count
);
```
