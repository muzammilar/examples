.PHONY: up test benchmark status cli down

PSQL := docker compose --progress quiet run --rm --no-TTY psql

# make benchmark: DEVICES x DAYS of one reading per device per minute (default 1000 x 7 = 10.08M
# rows), ingested once per WORKERS count by timescaledb-parallel-copy in batches of BATCH rows,
# then every query RUNS times on the rowstore, the columnstore and a continuous aggregate
SMOKE ?=
DEVICES ?= 1000
DAYS ?= $(if $(SMOKE),1,7)
WORKERS ?= $(if $(SMOKE),4,1 4 8)
BATCH ?= 5000
RUNS ?= $(if $(SMOKE),3,5)

up:
	docker compose up --detach --wait
	$(PSQL) -Atc "SELECT version()" -c "SELECT extname || ' ' || extversion FROM pg_extension ORDER BY 1"

# psql does not echo statements, so each sql/*.sql file is printed before its output
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(PSQL) -f /$$f || exit 1; done

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= timescaledb
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx timescaledb || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=timescaledb-single-$$(date -u +%Y%m%dT%H%M%SZ) DEVICES=$(DEVICES) DAYS=$(DAYS) \
		WORKERS="$(WORKERS)" BATCH=$(BATCH) RUNS=$(RUNS) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose --progress quiet run --rm -T bench && docker compose --progress quiet run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps
	$(PSQL) -c "SELECT hypertable_name, num_chunks, compression_enabled AS columnstore, pg_size_pretty(hypertable_size(format('%I.%I', hypertable_schema, hypertable_name)::regclass)) AS size FROM timescaledb_information.hypertables ORDER BY 1" \
		-c "SELECT job_id, application_name, schedule_interval, hypertable_name FROM timescaledb_information.jobs ORDER BY 1"

cli:
	docker compose run --rm psql

down:
	docker compose --profile tools --profile bench down --volumes
