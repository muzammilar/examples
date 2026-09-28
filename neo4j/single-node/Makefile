.PHONY: up test status cli benchmark down

SMOKE ?=

CYPHER := docker exec -i neo4j cypher-shell -u neo4j -p demo-password --format plain

up:
	docker compose up --detach --wait

# cypher/*.cypher in order: schema, MERGE nodes/relationships, paths, aggregation
test:
	for f in cypher/*.cypher; do echo "-- $$f"; cat $$f; echo "--"; $(CYPHER) < $$f || exit 1; done

status:
	$(CYPHER) "SHOW DATABASES YIELD name, currentStatus, role"

cli:
	docker exec -it neo4j cypher-shell -u neo4j -p demo-password

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= neo4j
export BENCH_CLIENT_CPUS ?= 2

# needs `make up`; SMOKE=1 for 10k persons (default 100k), or set N
benchmark:
	@docker compose ps --status running --services | grep -qx neo4j || { echo 'run `make up` first'; exit 1; }
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; SMOKE=$(SMOKE) HOST_UID=$$(id -u) HOST_GID=$$(id -g) docker compose run --rm bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

down:
	docker compose down --volumes
