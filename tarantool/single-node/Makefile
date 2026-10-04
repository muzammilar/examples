.PHONY: up test restart benchmark status cli logs down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

RUN := docker compose --progress quiet run --rm -T client scripts/run.lua

up:
	docker compose up --detach --wait
	$(MAKE) --no-print-directory status

# scripts/01-04 (schema, CRUD, stored procedures, transactions), then `restart`
test:
	$(RUN) scripts/01-spaces.lua scripts/02-crud.lua scripts/03-procedures.lua scripts/04-transactions.lua
	$(MAKE) --no-print-directory restart

# write rows, box.snapshot(), write more (WAL only), SIGKILL the container, start it, check
# every row came back from snapshot + WAL replay
restart:
	$(RUN) scripts/05-before-restart.lua
	docker kill --signal KILL tarantool
	docker compose up --detach --wait tarantool
	@docker logs tarantool 2>&1 | grep -E "recovering from|recover from|wasn't correctly closed|ready to accept" | tail -n 4
	$(RUN) scripts/06-after-restart.lua

# bench/bench.lua against the running instance, capped by bench/limits.sh
BENCH_CPUS ?= 2
BENCH_MEM ?= 2g
export BENCH_CLIENT_CPUS ?= 2
export FIBERS ?= 64
export CONNS ?= 4
export DURATION ?= 10
export KEYS ?= 100000

benchmark:
	@docker compose ps --status running --services | grep -qx tarantool || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) tarantool) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=tarantool-single-$$(date -u +%Y%m%dT%H%M%SZ); \
	docker compose --progress quiet run --rm -T bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	@echo 'return {version = box.info.version, status = box.info.status, ro = box.info.ro, lsn = box.info.lsn}' | \
		docker exec -i tarantool tt connect app:secret@127.0.0.1:3301 -f-

cli:
	docker exec -it tarantool tt connect app:secret@127.0.0.1:3301

logs:
	docker compose logs --follow

down:
	docker compose --profile test --profile bench down --volumes --remove-orphans --rmi local
