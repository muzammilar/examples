.PHONY: up build run status cli down

PSQL := docker compose --progress quiet run --rm --no-TTY psql

up:
	docker compose up --detach --wait
	$(PSQL) -Atc "SELECT version()"

build:
	docker compose --profile run build fleet

# generate VEHICLES x DAYS x (86400 / INTERVAL_S) readings, ingest them twice with WORKERS binary
# COPY connections (columnstore, rowstore), then time the dashboard queries on the rowstore, the
# columnstore and the continuous aggregate (RUNS runs each, median); results also go to results/
run:
	@mkdir -p results
	docker compose --progress quiet run --rm --build fleet | tee results/fleet-$$(date -u +%Y%m%dT%H%M%SZ).txt

status:
	docker compose ps
	$(PSQL) -c "SELECT hypertable_name, num_chunks, pg_size_pretty(hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass)) AS size FROM timescaledb_information.hypertables ORDER BY 1"

cli:
	docker compose run --rm psql

down:
	docker compose --profile run --profile tools down --volumes --rmi local
