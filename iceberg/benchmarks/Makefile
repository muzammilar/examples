.PHONY: up down status benchmarks benchmark reset

export BENCH_CPUS ?= 4
export BENCH_MEM ?= 6g
export CLICKHOUSE_VERSION ?= 26.10.2.103
export DUCKDB_VERSION ?= 1.5.5

COMPOSE := docker compose -f compose.yml
SYSTEM ?=
PROFILE ?= mixed
ROWS ?= 20000000
APPEND_ROWS ?=
UPDATE_FRACTION ?=
RUNS ?= 5
BATCHES ?= 1
SYSTEMS := duckdb-iceberg clickhouse-iceberg clickhouse-native

up:
	@test -n "$(SYSTEM)" || (echo 'set SYSTEM=duckdb-iceberg, clickhouse-iceberg, or clickhouse-native' && exit 2)
	$(COMPOSE) --profile $(SYSTEM) up -d $(if $(filter duckdb-iceberg,$(SYSTEM)),rest,$(SYSTEM))

down:
ifeq ($(SYSTEM),)
	@set -e; for system in $(SYSTEMS); do $(COMPOSE) --profile $$system down --remove-orphans; done
else
	$(COMPOSE) --profile $(SYSTEM) down --remove-orphans
endif

status:
	@for system in $(SYSTEMS); do echo "== $$system =="; $(COMPOSE) --profile $$system ps; done

benchmarks:
	go run ./cmd/benchctl $(if $(RUN_ID),--run-id $(RUN_ID),) --profile $(PROFILE) --rows $(ROWS) --runs $(RUNS) --batches $(BATCHES) --cpus $(BENCH_CPUS) --memory $(BENCH_MEM) \
		$(if $(APPEND_ROWS),--append-rows $(APPEND_ROWS),) \
		$(if $(UPDATE_FRACTION),--update-fraction $(UPDATE_FRACTION),)

benchmark: benchmarks

# Removes the persistent warehouse, generated inputs, and ClickHouse data.
reset: down
	@for volume in iceberg-bench-warehouse iceberg-bench-inputs iceberg-bench-ch-iceberg-data iceberg-bench-ch-native-data; do docker volume rm $$volume 2>/dev/null || true; done
