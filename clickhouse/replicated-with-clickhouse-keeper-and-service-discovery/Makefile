.PHONY: up test schema status add-node remove-node nodes cli down

NAME ?= clickhouse-server-12
SHARD ?= 001

up:
	docker compose up --detach --wait

# discovery, Replicated databases + tables, inserts on two shards, per-replica counts, Distributed read
test:
	./scripts/test.sh

# only (re)create the schema on the current members; test does this too
schema:
	./scripts/schema.sh

status:
	docker exec -i clickhouse-server-01 clickhouse-client --format PrettyCompact < sql/0-discovery.sql

# add a data node at runtime, e.g. `make add-node NAME=clickhouse-server-13 SHARD=002`
# it registers itself, gets the schema from its Replicated databases and fetches the shard's data
add-node:
	./scripts/add-node.sh $(NAME) $(SHARD)
	@$(MAKE) --no-print-directory status

# remove a data node, e.g. `make remove-node NAME=clickhouse-server-13`
# stops it, waits for discovery to drop it, then deletes its replica metadata from Keeper
remove-node:
	./scripts/remove-node.sh $(NAME)
	@$(MAKE) --no-print-directory status

# nodes added with add-node
nodes:
	docker ps --filter label=com.docker.compose.project=ch-discovery --filter label=com.docker.compose.service=clickhouse-node --format 'table {{.Names}}\t{{.Status}}'

cli:
	docker exec -it clickhouse-server-01 clickhouse-client

down:
	docker compose --profile nodes down --volumes --remove-orphans
