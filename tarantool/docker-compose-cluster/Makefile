.PHONY: up test failover benchmark status cli logs down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

INSTANCES := 1 2 3
# Lua on instance $(1) through its console (tt connect), YAML output
LUA = echo '$(2)' | docker exec -i tarantool-$(1) tt connect app:secret@tarantool-$(1):3301 -f-
# seconds since the epoch, or since $(1) if given (BSD date has no %N)
NOW = perl -MTime::HiRes=time -e 'printf "%.1f", time - ($$ARGV[0] // 0)'
# container name of the current leader (empty while there is none)
LEADER = $$(for i in $(INSTANCES); do $(call LUA,$$i,return box.info.election.state) 2>/dev/null | \
	grep -q '^- leader' && echo tarantool-$$i; done | head -n 1)

up:
	docker compose up --detach --wait
	@for t in $$(seq 1 30); do [ -n "$(LEADER)" ] && break; sleep 1; done
	$(MAKE) --no-print-directory status

# replication links, 1,000 synchronous writes on the leader, the same rows on every instance
test:
	docker compose --progress quiet run --rm -T client scripts/run.lua \
		scripts/01-replication.lua scripts/02-sync-writes.lua scripts/03-every-instance.all.lua

# scripts/load.lua writes to the leader for LOAD_DURATION s; after 10 s the leader is killed
# (SIGKILL), the other two elect a new one, writers reconnect; after 25 s the old leader is
# started again and catches up as a follower. load.lua then checks no acknowledged write is lost.
export LOAD_DURATION ?= 40
export WRITERS ?= 16
failover:
	@docker compose --progress quiet run --rm -T load & load=$$!; \
	sleep 10; old=$(LEADER); \
	echo "==> $$(date -u +%T) docker kill $$old (leader)"; docker kill --signal KILL $$old >/dev/null; \
	t0=$$($(NOW)); new=""; while [ -z "$$new" ]; do sleep 0.2; new=$(LEADER); done; \
	echo "==> $$(date -u +%T) new leader: $$new, $$($(NOW) $$t0) s after the kill (polling box.info.election.state)"; \
	sleep 15; echo "==> $$(date -u +%T) starting $$old"; \
	docker compose up --detach --wait $$old 2>/dev/null; \
	echo "==> $$(date -u +%T) $$old is up"; \
	wait $$load; rc=$$?; $(MAKE) --no-print-directory status; exit $$rc

# bench/bench.lua against the leader; the three instances capped by bench/limits.sh
BENCH_CPUS ?= 6
BENCH_MEM ?= 3g
export BENCH_CLIENT_CPUS ?= 2
export FIBERS ?= 64
export CONNS ?= 4
export DURATION ?= 10
export KEYS ?= 100000
benchmark:
	@docker compose ps --status running --services | grep -qx tarantool-1 || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) tarantool-1 tarantool-2 tarantool-3) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=tarantool-cluster-$$(date -u +%Y%m%dT%H%M%SZ); \
	docker compose --progress quiet run --rm -T bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	@for i in $(INSTANCES); do \
		docker inspect -f '{{.State.Running}}' tarantool-$$i 2>/dev/null | grep -q true || { echo "tarantool-$$i: down"; continue; }; \
		$(call LUA,$$i,return cluster_status()) 2>/dev/null | sed -n 's/^- //p'; \
	done

cli:
	docker exec -it $(LEADER) tt connect app:secret@$(LEADER):3301

logs:
	docker compose logs --follow

down:
	docker compose --profile test --profile bench down --volumes --remove-orphans --rmi local
