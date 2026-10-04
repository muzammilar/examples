.PHONY: up test durability status cli lab logs benchmark down

SMOKE ?=
LAB_PORT ?= 3010

MG := docker exec -i memgraph-single mgconsole --output-format=tabular
# one query, CSV output without the header row
MGQ = echo "$(1)" | docker exec -i memgraph-single mgconsole --output-format=csv | tail -n +2 | tr -d '"'

up:
	docker compose up --detach --wait
	@echo "Bolt: bolt://127.0.0.1:$${MEMGRAPH_PORT:-7689}  Lab: http://127.0.0.1:$(LAB_PORT)"

# cypher/*.cypher in order: schema, create, queries, MAGE algorithms
test:
	@for f in cypher/*.cypher; do echo "-- $$f"; $(MG) < $$f || exit 1; done
	@echo "-- duplicate code (must be rejected by the uniqueness constraint)"
	@if echo "CREATE (:Airport {code: 'FRA', city: 'again'});" | $(MG) 2>&1 | tee /dev/stderr | grep -q 'unique constraint violation'; \
		then echo "OK: rejected"; else echo "FAIL: duplicate accepted"; exit 1; fi

# snapshot + WAL: write, snapshot, write more (WAL only), SIGKILL (no snapshot on exit), restart, count
durability:
	@$(call MGQ,MATCH (n:Durable) DETACH DELETE n;) >/dev/null
	@$(call MGQ,UNWIND range(1, 50000) AS i CREATE (:Durable {i: i, phase: 'before-snapshot'});) >/dev/null
	@echo "-- CREATE SNAPSHOT"; echo "CREATE SNAPSHOT;" | $(MG)
	@for i in $$(seq 1 1000); do echo "CREATE (:Durable {i: $$i, phase: 'wal-only'});"; done | \
		docker exec -i memgraph-single mgconsole >/dev/null
	@before=$$($(call MGQ,MATCH (n:Durable) RETURN count(n);)); echo "-- before kill: $$before :Durable nodes (50000 in the snapshot, 1000 single-row transactions after it)"; \
	docker kill --signal KILL memgraph-single >/dev/null; echo "-- SIGKILL sent; starting again"; \
	t0=$$(date +%s); docker compose up --detach --wait memgraph >/dev/null 2>&1; t1=$$(date +%s); \
	after=$$($(call MGQ,MATCH (n:Durable) RETURN count(n);)); \
	echo "-- after restart ($$((t1 - t0)) s until healthy): $$after :Durable nodes"; \
	echo "SHOW SNAPSHOTS;" | $(MG); \
	[ "$$before" = "$$after" ] && echo "OK: nothing lost" || { echo "FAIL: lost $$((before - after))"; exit 1; }

status:
	@echo "SHOW VERSION; SHOW STORAGE INFO; SHOW REPLICATION ROLE; SHOW LICENSE INFO;" | $(MG)
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

cli:
	docker exec -it memgraph-single mgconsole

lab:
	@echo "Memgraph Lab: http://127.0.0.1:$(LAB_PORT) (Quick connect points at the memgraph container)"
	@open http://127.0.0.1:$(LAB_PORT) 2>/dev/null || true

logs:
	docker compose logs --follow memgraph

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM for the
# containers in BENCH_SPEC, applied with `docker update` and restored afterwards
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= memgraph
export BENCH_CLIENT_CPUS ?= 2

# needs `make up`; SMOKE=1 for 10k persons (default 100k), or set N
benchmark:
	@docker compose ps --status running --services | grep -qx memgraph || { echo 'run `make up` first'; exit 1; }
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; SMOKE=$(SMOKE) HOST_UID=$$(id -u) HOST_GID=$$(id -g) docker compose run --rm bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

down:
	docker compose --profile bench down --volumes --remove-orphans --rmi local
