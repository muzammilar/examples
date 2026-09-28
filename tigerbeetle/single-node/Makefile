.PHONY: up test benchmark status cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

# built-in REPL client inside the replica's container, minus its connection log lines
REPL = docker exec tigerbeetle timeout 30 /tigerbeetle repl --cluster=0 --addresses=3000 \
	--command='$(1)' 2>&1 | grep -v 'info(message_bus)'

up:
	docker compose up --detach --wait
	docker compose logs --no-log-prefix tigerbeetle | grep -E 'listening on|Allocated'

# client/demo.py through the official Python client (each step prints its title first),
# then one account through the built-in REPL
test:
	docker compose --progress quiet run --rm --build --no-deps client
	@echo; echo "==== tigerbeetle repl: lookup_accounts id=2;"
	$(call REPL,lookup_accounts id=2;)

# `tigerbeetle benchmark` against the running replica: TRANSFERS transfers (SMOKE=1: 100k)
# between ACCOUNTS accounts from CLIENTS clients, in batches of up to BATCH (8189 = the most one
# request holds); prints a summary table, keeps results/tigerbeetle-single-<UTC time>.{txt,json}
SMOKE ?=
TRANSFERS ?= $(if $(filter 1,$(SMOKE)),100000,1000000)
ACCOUNTS ?= 10000
CLIENTS ?= 1
BATCH ?= 8189

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= tigerbeetle
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx tigerbeetle || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=tigerbeetle-single-$$(date -u +%Y%m%dT%H%M%SZ) TRANSFERS=$(TRANSFERS) ACCOUNTS=$(ACCOUNTS) \
		CLIENTS=$(CLIENTS) BATCH=$(BATCH) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T --no-deps bench && docker compose run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec tigerbeetle /tigerbeetle version
	$(call REPL,lookup_accounts id=1$(,) id=2$(,) id=3;) | grep -E '"(id|debits_posted|credits_posted)"' | paste - - -
, := ,

cli:
	docker exec -it tigerbeetle /tigerbeetle repl --cluster=0 --addresses=3000

down:
	docker compose --profile test --profile bench down --volumes --rmi local
