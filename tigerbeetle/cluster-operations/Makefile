.PHONY: up test status scale-out standby scale-in replace rolling-restart benchmark cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -ec

ADDRESSES := 10.203.54.10:3000,10.203.54.11:3000,10.203.54.12:3000
LOGS = docker logs $$(docker compose ps -q $(1)) 2>&1
# DURATION: seconds of client/load.py during scale-out, scale-in, replace, rolling-restart
export DURATION ?= 40

up:
	docker compose up --detach --wait
	$(MAKE) --no-print-directory status

test:
	docker compose --progress quiet run --rm --build client

status:
	docker compose --profile standby ps --format 'table {{.Service}}\t{{.Status}}'
	@for s in $$(docker compose --profile standby ps --services --status running); do \
		echo "$$s: $$($(call LOGS,$$s) | grep -o 'transition_to_normal.*' | tail -n 1)"; done

# 3 actives -> 3 actives + 2 standbys -> 3 actives, under load (ops.sh)
scale-out:
	./ops.sh scale-out
standby:
	./ops.sh standby
scale-in:
	./ops.sh scale-in

# lose replica R's data file (default: a backup) and `tigerbeetle recover` it, under load
R ?=
replace:
	./ops.sh replace $(R)

# recreate every replica one at a time under load, e.g. with CACHE_GRID=1GiB
rolling-restart:
	./ops.sh rolling-restart

# tigerbeetle benchmark against the three actives (client 2 CPUs; each replica cpus: 2, MEM_LIMIT)
BENCH_ARGS ?= --transfer-count=1000000 --account-count=10000
benchmark:
	@mkdir -p results; out=results/bench-$$(date -u +%Y%m%dT%H%M%SZ).txt; \
	echo "==> tigerbeetle benchmark $(BENCH_ARGS); replica-0: $$($(call LOGS,replica-0) | grep -o 'Grid cache.*' | tail -n 1)"; \
	BENCH_ARGS="$(BENCH_ARGS)" docker compose --progress quiet run --rm -T bench > $$out 2>&1; \
	grep -E 'batches in|load accepted|latency p(50|99|100)|queries in' $$out; echo "(full output: $$out)"

cli:
	docker compose exec replica-0 /tigerbeetle repl --cluster=0 --addresses=$(ADDRESSES)

down:
	docker compose --profile standby --profile test --profile bench down --volumes --rmi local
