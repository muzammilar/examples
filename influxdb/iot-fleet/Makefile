.PHONY: up build run files status down

up:
	docker compose up --detach --wait
	docker compose logs --no-log-prefix influxdb3 | grep -E 'server starting|startup time'

build:
	docker compose --profile run build app

# runs the app against the running server; it recreates database `iot` each run
# (override CARDINALITIES, ROWS_PER_LEVEL, WRITERS, BATCH, FLEET_DEVICES, FLEET_POINTS,
# HISTORY_DEVICES, HISTORY_DAYS, HISTORY_STEP_S, ITER)
run:
	docker compose --progress quiet run --rm --build --no-deps app

# what the object store holds per prefix (WAL, Parquet under dbs/, snapshots, catalog)
files:
	@docker exec influxdb3-iot-minio sh -c 'mc alias set local http://localhost:9000 minioadmin minioadmin >/dev/null && \
		mc du --depth 2 local/influxdb3/node0'

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	curl -s localhost:$${INFLUXDB_PORT:-8192}/ping; echo
	docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' influxdb3-iot influxdb3-iot-minio

down:
	docker compose --profile run down --volumes --rmi local
