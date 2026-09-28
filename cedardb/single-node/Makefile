.PHONY: up test benchmark status cli down

PSQL := docker compose --progress quiet run --rm --no-TTY psql

# make benchmark: seconds per pgbench run, client counts, pgbench scale (x 100k accounts),
# tries per transaction on serialization failures, runs per analytic query (SMOKE=1: a short run)
SMOKE ?=
DURATION ?= $(if $(SMOKE),10,60)
CLIENTS ?= 1 8 16
SCALE ?= $(if $(SMOKE),2,10)
MAX_TRIES ?= 10
RUNS ?= $(if $(SMOKE),3,5)

up:
	docker compose up --detach --wait
	$(PSQL) -Atc 'SELECT version()'

# psql does not echo statements, so each sql/*.sql file is printed before its output
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(PSQL) -f /$$f || exit 1; done

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= cedardb
export BENCH_CLIENT_CPUS ?= 2

# pgbench TPC-B-like and select-only at each CLIENTS count for DURATION s, then the
# bench/analytics.sql queries over 3M generated orders RUNS times each
benchmark:
	@docker compose ps --status running --services | grep -qx cedardb || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=cedardb-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) CLIENTS="$(CLIENTS)" SCALE=$(SCALE) \
		MAX_TRIES=$(MAX_TRIES) RUNS=$(RUNS) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	echo "meta: server_version=$$($(PSQL) -Atc 'SELECT version()')" > results/$$NAME.txt; \
	docker compose --progress quiet run --rm -T bench && docker compose --progress quiet run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps
	$(PSQL) -c "SELECT relname AS table, pg_size_pretty(pg_total_relation_size(oid)) AS size FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace ORDER BY 1"

cli:
	docker compose run --rm psql

down:
	docker compose --profile bench down --volumes
