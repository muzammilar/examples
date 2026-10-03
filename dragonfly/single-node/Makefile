.PHONY: up test run-local scripts bench bench-resp bench-lua bench-local cli down

DRAGONFLY_VERSION ?= v2.0.0
DRAGONFLY_PORT ?= 6391
DRAGONFLY_ADDR ?= 127.0.0.1:$(DRAGONFLY_PORT)
GO_VERSION ?= 1.26
export

BENCH_N ?= 200000
BENCH_C ?= 50
BENCH_KEYS ?= 100000
LUA_FLAGS = -n $(BENCH_N) -c $(BENCH_C) -keys $(BENCH_KEYS)

# valkey-benchmark at pipeline 1 and 16; RESP_TABLE turns its CSV into op, ops/s, p50, p99
RESP_LOOP = for p in 1 16; do echo "pipeline $$p"; valkey-benchmark $(1) \
	-t set,get,incr,lpush,hset -n $(BENCH_N) -c $(BENCH_C) -r $(BENCH_KEYS) -P $$p -q --csv; done
RESP_TABLE = tr -d '"\r' | awk -F, '/^pipeline/ { printf "%s\n%-8s %9s %7s %7s\n", $$0, "op", "ops/s", "p50 ms", "p99 ms"; next } \
	$$1 != "test" && NF > 6 { n++; printf "%-8s %9.0f %7.2f %7.2f\n", $$1, $$2, $$5, $$7 } END { exit !n }'

# the dragonfly image ships redis-cli
CLI := docker compose exec -T dragonfly redis-cli

up:
	docker compose up --detach --wait dragonfly

# build the Go program into an image and run it once against dragonfly
test:
	docker compose --progress quiet --profile app run --build --rm app

run-local:
	go run .

# SCRIPT LOAD each script and print its SHA1
scripts:
	for f in lua/*.lua; do printf "%s " $$f; $(CLI) -x script load < $$f; done

bench: bench-resp bench-lua

bench-resp:
	docker compose --progress quiet --profile bench run --rm -T resp sh -c '$(call RESP_LOOP,-h dragonfly)' | $(RESP_TABLE)

# go/rueidis-lua-bench, linked as bench
bench-lua:
	docker compose --progress quiet --profile bench run --build --rm lua -addr dragonfly:6379 $(LUA_FLAGS)

bench-local:
	cd bench && go run . -addr $(DRAGONFLY_ADDR) $(LUA_FLAGS)

cli:
	docker compose exec dragonfly redis-cli

down:
	docker compose --profile app --profile bench down --volumes --rmi local
