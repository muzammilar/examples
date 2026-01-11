.PHONY: up down results

up:
	docker-compose up --detach

down:
	docker-compose down --volumes

results:
	docker exec clickhouse clickhouse-client --echo --format Pretty --multiline --multiquery --queries-file /docker-entrypoint-initdb.d/02-query-dictionary.sql
