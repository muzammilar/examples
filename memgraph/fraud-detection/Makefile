.PHONY: up build run status cli cli-neo4j down

SHELL := /bin/bash

up:
	docker compose up --detach --wait

build:
	docker compose --profile run build app

# both engines in turn (ENGINES=memgraph or ENGINES=neo4j for one); sizes: ACCOUNTS, DEVICES,
# TRANSFERS, STREAM, WORKERS, RUNS. Output is also saved to results/run-<time>.txt
run:
	@mkdir -p results
	set -o pipefail; docker compose --progress quiet run --rm --build --no-deps app | tee results/run-$$(date +%Y%m%dT%H%M%S).txt

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	@echo "SHOW STORAGE INFO;" | docker exec -i memgraph-fraud mgconsole --output-format=csv | grep -E 'memory_res|vertex|edge' || true

cli:
	docker exec -it memgraph-fraud mgconsole

cli-neo4j:
	docker exec -it memgraph-fraud-neo4j cypher-shell -u neo4j -p demo-password

down:
	docker compose --profile run down --volumes --remove-orphans --rmi local
