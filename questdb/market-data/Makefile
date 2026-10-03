.PHONY: up build run status down

up:
	docker compose up --detach --wait
	@echo "web console: http://localhost:$${QUESTDB_HTTP_PORT:-9001}"

# builds the Rust app (first run: a few minutes)
build:
	docker compose --profile run build app

# live ingest + concurrent queries against the running server; recreates its tables each run
# (override FEEDS, DURATION, SYMBOLS, BATCH, RATE, QUERY_CLIENTS, KEEP=1)
run:
	docker compose --progress quiet run --rm --build --no-deps app

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec questdb-market curl -sfG http://localhost:9000/exec --data-urlencode 'query=SELECT build()' | grep -oE 'QuestDB [0-9.]+'

down:
	docker compose --profile run down --volumes --rmi local
