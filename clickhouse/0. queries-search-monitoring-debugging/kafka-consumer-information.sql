-- kafka consumer stats for the kafka table (like number of messages read, bytes processed, etc)
SELECT hostName() AS host, table, consumer_id, last_poll_time, num_messages_read, num_rebalance_assignments
FROM clusterAllReplicas('cluster_name', system.kafka_consumers)
WHERE database = 'my_database' AND table = 'kafka_destination_table_1'
ORDER BY host

-- table relationships and dependencies
SELECT name, dependencies_database, dependencies_table
FROM system.tables
WHERE database = 'my_database'

-- table relationships and dependencies
SELECT name, dependencies_database, dependencies_table
FROM system.tables
WHERE database = 'my_database' AND name = 'kafka_destination_table_1'

-- kafka consumer groups names
SELECT
    name,
    extract(engine_full, 'kafka_topic_list\\s*=\\s*\'([^\']*)\'') AS topic,
    extract(engine_full, 'kafka_group_name\\s*=\\s*\'([^\']*)\'') AS group_name
FROM system.tables
WHERE database = 'my_database'
  AND name IN (
      'kafka_destination_table_1', 'kafka_destination_table_2', 'kafka_destination_table_3',
      'kafka_destination_table_4', 'kafka_destination_table_5', 'kafka_destination_table_6'
  )
ORDER BY name
