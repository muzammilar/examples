.PHONY: up down results-dict results-analytics

up:
	docker-compose up --detach

down:
	docker-compose down --volumes

results-dict:
	docker exec clickhouse clickhouse-client --echo --format Pretty --multiline --multiquery --queries-file /queries/01-query-dictionary.sql

results-analytics:
	docker exec clickhouse clickhouse-client --echo --format Pretty --multiline --multiquery --queries-file /queries/02-query-kafka-analytics.sql
