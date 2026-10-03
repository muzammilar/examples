.PHONY: up test files benchmark status cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

# the influxdb3 CLI inside the server container, authenticated with the offline admin token
TOKEN = $$(docker exec influxdb3-single sed -n 's/.*"token": *"\([^"]*\)".*/\1/p' /token/admin.json)
INFLUXDB3 = docker exec -e INFLUXDB3_AUTH_TOKEN=$(TOKEN) influxdb3-single influxdb3

up:
	docker compose up --detach --wait
	docker compose logs --no-log-prefix influxdb3 | grep -E 'server starting|Admin token|startup time'

# scripts/demo.sh: auth, database, line protocol writes, SQL, InfluxQL, last/distinct value
# caches, a WAL and a schedule plugin, Parquet files; then the bucket as MinIO sees it
test:
	docker compose --progress quiet run --rm --no-deps client
	@$(MAKE) --no-print-directory files

# objects in the S3 bucket (dbs/<db id>/<table id>/<date>/<10 min chunk>/<snapshot>.parquet);
# WAL files only counted: one per second with writes, the last 300 snapshotted ones are kept
files:
	@echo; echo "==== s3://influxdb3 (MinIO), without node0/wal/ and catalog logs"
	@docker exec influxdb3-single-minio sh -c 'mc alias set local http://localhost:9000 minioadmin minioadmin >/dev/null && mc ls -r local/influxdb3' \
		| grep -vE 'node0/wal/|catalog/v3/logs/'
	@docker exec influxdb3-single-minio sh -c 'echo "node0/wal/: $$(mc ls -r local/influxdb3/node0/wal/ | wc -l) WAL files"'

# bench/bench.py: ROWS line protocol rows for SERIES hosts (SMOKE=1: 200k rows, 1k hosts) from
# WRITERS writers in requests of BATCH lines (NO_SYNC=1: ack before the WAL flush), then ITER runs
# of each query (SQL vs the caches)
SMOKE ?=
ROWS ?= $(if $(filter 1,$(SMOKE)),200000,2000000)
SERIES ?= $(if $(filter 1,$(SMOKE)),1000,10000)
BATCH ?= 10000
WRITERS ?= 16
NO_SYNC ?=
ITER ?= 30

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount taken off the top),
# set with `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= minio=1:1g influxdb3
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx influxdb3 || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=influxdb3-single-$$(date -u +%Y%m%dT%H%M%SZ) ROWS=$(ROWS) SERIES=$(SERIES) \
		BATCH=$(BATCH) WRITERS=$(WRITERS) NO_SYNC=$(NO_SYNC) ITER=$(ITER) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T --no-deps bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	curl -s localhost:$${INFLUXDB_PORT:-8191}/ping; echo
	$(INFLUXDB3) show databases

# interactive shell in the server container with INFLUXDB3_AUTH_TOKEN set (try `influxdb3 query -d home 'SHOW TABLES'`)
cli:
	docker exec -it -e INFLUXDB3_AUTH_TOKEN=$(TOKEN) influxdb3-single bash

down:
	docker compose --profile test --profile bench down --volumes
