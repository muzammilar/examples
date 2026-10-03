.PHONY: up test failover benchmark status cli down

SHELL := bash
PSQL := docker compose --progress quiet run --rm --no-TTY psql

up:
	docker compose up --detach --wait
	$(PSQL) -Atc "SELECT version()" -c "SELECT 'timescaledb ' || extversion FROM pg_extension WHERE extname = 'timescaledb'"

# psql does not echo statements, so each sql/*.sql file is printed before its output
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(PSQL) -f /$$f || exit 1; done

# stop dn2, read/write around it, start it, re-replicate the chunks it missed
failover:
	bash failover.sh

# make benchmark: DEVICES x DAYS x 1/minute rows loaded with WORKERS timescaledb-parallel-copy
# workers into a local hypertable, a distributed one with replication factor 1 and one with 2;
# then bench/queries.sql RUNS times on each
SMOKE ?=
DEVICES ?= 1000
DAYS ?= $(if $(SMOKE),1,2)
WORKERS ?= 4
BATCH ?= 5000
RUNS ?= $(if $(SMOKE),3,5)

# resource budget (bench/limits.sh): BENCH_CPUS / BENCH_MEM split equally over BENCH_SPEC
BENCH_CPUS ?= 8
BENCH_MEM ?= 8g
BENCH_SPEC ?= an1 dn1 dn2 dn3
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx an1 || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=timescaledb-multinode-$$(date -u +%Y%m%dT%H%M%SZ) DEVICES=$(DEVICES) DAYS=$(DAYS) \
		WORKERS=$(WORKERS) BATCH=$(BATCH) RUNS=$(RUNS) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose --progress quiet run --rm -T bench && docker compose --progress quiet run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	$(PSQL) -c "SELECT node_name, options FROM timescaledb_information.data_nodes ORDER BY 1" \
		-c "SELECT hypertable_name, is_distributed, replication_factor, num_chunks FROM timescaledb_information.hypertables ORDER BY 1"

cli:
	docker compose run --rm psql

down:
	docker compose --profile tools --profile bench down --volumes
