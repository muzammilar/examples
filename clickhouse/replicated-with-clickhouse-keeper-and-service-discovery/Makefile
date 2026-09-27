.PHONY: up test status scale-out cli down

up:
	docker compose up --detach --wait

# discovery, ON CLUSTER schema, inserts on two shards, per-replica counts, Distributed read
test:
	./scripts/test.sh

status:
	docker compose exec -T clickhouse-server-01 clickhouse-client --format PrettyCompact < sql/0-discovery.sql

# start clickhouse-server-12 (SHARD=001); it registers itself and appears in cluster_hits with no config change.
# ON CLUSTER DDL is not replayed for late joiners, so re-apply the (idempotent) schema; the new replica then
# fetches the existing parts of shard 001 from its peers.
scale-out:
	docker compose --profile scale-out up --detach --wait clickhouse-server-12
	docker compose exec -T clickhouse-server-12 clickhouse-client < sql/1-schema-base-tables.sql > /dev/null
	docker compose exec -T clickhouse-server-12 clickhouse-client < sql/2-schema-distributed-tables.sql > /dev/null
	$(MAKE) status

cli:
	docker compose exec clickhouse-server-01 clickhouse-client

down:
	docker compose --profile scale-out down --volumes --remove-orphans
