DRAGONFLY_VERSION ?= v2.0.0
VALKEY_VERSION ?= 9.1.2-alpine
export DRAGONFLY_VERSION VALKEY_VERSION

BENCH_N ?= 200000
BENCH_C ?= 50
BENCH_KEYS ?= 100000
LUA_FLAGS = -n $(BENCH_N) -c $(BENCH_C) -keys $(BENCH_KEYS)

# valkey-benchmark at pipeline 1 and 16; RESP_TABLE turns its CSV into op, ops/s, p50, p99
RESP_LOOP = for p in 1 16; do echo "pipeline $$p"; valkey-benchmark $(1) \
	-t set,get,incr,lpush,hset -n $(BENCH_N) -c $(BENCH_C) -r $(BENCH_KEYS) -P $$p -q --csv; done
RESP_TABLE = tr -d '"\r' | awk -F, '/^pipeline/ { printf "%s\n%-8s %9s %7s %7s\n", $$0, "op", "ops/s", "p50 ms", "p99 ms"; next } \
	$$1 != "test" && NF > 6 { n++; printf "%-8s %9.0f %7.2f %7.2f\n", $$1, $$2, $$5, $$7 } END { exit !n }'

.PHONY: up test migrate failover status bench bench-resp bench-lua cli emulated down

up:
	docker compose up --detach --wait
	./cluster.sh init

# CLUSTER INFO/SLOTS/SHARDS on all nodes, 1000 keys over 3 shards via valkey-cli -c, MOVED, replicas in sync
test:
	./cluster.sh test

# move 1000 slots from the master of slot 0 to the master of slot 8192 (DFLYCLUSTER CONFIG with migrations)
migrate:
	./cluster.sh migrate

# stop the master of slot 0, promote its replica with a new config; restart it as the new replica
failover:
	./cluster.sh failover

status:
	./cluster.sh status

bench: bench-resp bench-lua

# valkey-benchmark --cluster reads the slots from CLUSTER NODES, like against Valkey
bench-resp:
	docker compose --progress quiet run --rm -T tools \
		sh -c '$(call RESP_LOOP,-h dragonfly-1 --cluster)' | $(RESP_TABLE)

bench-lua:
	docker compose --progress quiet --profile bench run --build --rm lua -addr dragonfly-1:6379 $(LUA_FLAGS)

cli:
	docker compose --progress quiet run --rm tools valkey-cli -h dragonfly-1 -c

emulated:
	docker compose --profile emulated up --detach --wait dragonfly-emulated
	docker compose --progress quiet run --rm -T tools valkey-cli -h dragonfly-emulated cluster shards

down:
	docker compose --profile tools --profile emulated --profile bench down --volumes --rmi local
