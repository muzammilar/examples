.PHONY: up test status cli benchmark down

SMOKE ?=
# TPC-H scale factor: 1 (~1 GB of CSV-equivalent data), 0.1 with SMOKE=1
SF ?= $(if $(filter-out 0,$(SMOKE)),0.1,1)
# resource budget for `make benchmark`: DuckDB runs inside the bench container, so these are
# its compose cpus/mem_limit (the same single-node budget as the server databases in this repo)
export BENCH_CPUS ?= 4
export BENCH_MEM ?= 6g

export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose --progress quiet --profile tools
DUCKDB := $(COMPOSE) run --rm -T duckdb

# also runs the CREATE VIEW lines of sql/01 (single source), so `make cli` has the
# views in data/demo.duckdb right after `make up`, before `make test` has run
up:
	$(COMPOSE) pull
	mkdir -p data
	test -f data/events.csv || $(COMPOSE) run --rm gen
	grep '^CREATE OR REPLACE VIEW' sql/01-read-files.sql | $(DUCKDB) demo.duckdb

# the duckdb CLI does not echo statements, so each sql/*.sql file is printed before
# its output; all files share data/demo.duckdb (01 creates the views the rest use)
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(DUCKDB) demo.duckdb < $$f || exit 1; done

status:
	$(DUCKDB) -c "SELECT version() AS duckdb, platform FROM pragma_platform()" \
		-c "SELECT 'events.csv' AS file, count(*) AS rows FROM 'events.csv' \
		    UNION ALL SELECT 'users.json', count(*) FROM 'users.json' \
		    UNION ALL SELECT 'countries.csv', count(*) FROM 'countries.csv'" \
		-c "SELECT split_part(filename[7:], '/', 1) AS path, count(*) AS files, \
		    format_bytes(sum(size)::BIGINT) AS size FROM read_blob('/data/**') GROUP BY ALL ORDER BY path"

cli:
	$(COMPOSE) run --rm duckdb demo.duckdb

# TPC-H via the tpch extension in a separate database file (data/bench/tpch.duckdb, deleted
# afterwards): dbgen, all 22 queries x3, EXPORT DATABASE to Parquet, a subset again on Parquet
benchmark:
	rm -rf data/bench && mkdir -p data/bench/prof results
	sh bench/tpch.sh $(SF) $$(date -u +%Y%m%dT%H%M%SZ) $$(docker info --format '{{.NCPU}} {{.MemTotal}}') \
		$(BENCH_CPUS) $(BENCH_MEM) \
		| docker compose --progress quiet --profile bench run --rm -T bench
	rm -rf data/bench

down:
	$(COMPOSE) down --volumes --remove-orphans
	rm -rf data
