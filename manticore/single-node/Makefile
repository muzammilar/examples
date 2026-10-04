.PHONY: up test benchmark status cli logs down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

# mysql client inside the container; --comments keeps optimizer hints (/*+ ... */)
SQL = docker exec -i manticore-single mysql -h127.0.0.1 -P9306 --table --comments
ROWS ?= 1000000

up:
	docker compose up --detach --wait
	docker exec manticore-single mysql -h127.0.0.1 -P9306 --table -e 'SHOW VERSION'

# sql/*.sql over the MySQL protocol (each file echoed, then its output), then
# scripts/07-columnar.sh (ROWS generated rows into a row-wise and a columnar table) and
# scripts/08-http.sh (the HTTP JSON API)
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; $(SQL) -v < $$f || exit 1; done
	@echo; echo "==== scripts/07-columnar.sh"
	docker exec -e ROWS=$(ROWS) manticore-single sh /scripts/07-columnar.sh
	@echo; echo "==== scripts/08-http.sh"
	docker exec manticore-single sh /scripts/08-http.sh

# bench/run.sh: manticore-load ingest + queries from a separate container (DOCS, THREADS,
# QUERIES, KNN_QUERIES, KEEP=1); results/manticore-single-<UTC time>.txt
# resource budget (bench/limits.sh): BENCH_CPUS / BENCH_MEM for the manticore container
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
export BENCH_CLIENT_CPUS ?= 4
DOCS ?=
THREADS ?=
QUERIES ?=
KNN_QUERIES ?=
KEEP ?=

benchmark:
	@docker compose ps --status running --services | grep -qx manticore || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) manticore) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=manticore-single-$$(date -u +%Y%m%dT%H%M%SZ) DOCS=$(DOCS) THREADS=$(THREADS) \
		QUERIES=$(QUERIES) KNN_QUERIES=$(KNN_QUERIES) KEEP=$(KEEP) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec manticore-single mysql -h127.0.0.1 -P9306 --table -e "SHOW VERSION; SHOW TABLES; SHOW STATUS LIKE 'uptime'; SHOW STATUS LIKE 'queries'"

cli:
	docker exec -it manticore-single mysql -h127.0.0.1 -P9306

logs:
	docker compose logs --follow

down:
	docker compose --profile bench down --volumes --remove-orphans --rmi local
