.PHONY: up test benchmark status cli down

PD_CTL := docker exec tikv-pd0 /pd-ctl -u http://pd0:2379
# pd-ctl prints JSON; jq (inside the PD image) turns it into one line per store / region
PD_JQ := docker exec -i tikv-pd0 jq -r
STORES_UP = $$($(PD_CTL) store | $(PD_JQ) '[.stores[] | select(.store.state_name == "Up")] | length')
UNDER_REPLICATED = $$($(PD_CTL) region | $(PD_JQ) '[.regions[] | select((.peers | length) < 3)] | length')

up:
	docker compose up --detach --wait
	docker compose build client
	@echo "==> waiting until PD sees 3 stores Up (first heartbeat after ~10 s) and every region has 3 replicas"
	for i in $$(seq 90); do [ "$(STORES_UP)" = 3 ] && [ "$(UNDER_REPLICATED)" = 0 ] && break; sleep 2; done
	[ "$(STORES_UP)" = 3 ] && [ "$(UNDER_REPLICATED)" = 0 ]
	$(PD_CTL) store | $(PD_JQ) '.stores[] | "store \(.store.id) \(.store.address) \(.store.state_name)"'

# client/main.go: RawKV put/get/scan/delete, TxnKV commit + optimistic and pessimistic conflicts
test:
	docker compose run --rm client

# go-ycsb against PD + TiKV, RawKV then TxnKV: load RECORDS rows, run YCSB workload A (50% read,
# 50% update) and C (read only) with OPERATIONS operations each on THREADS threads (SMOKE=1:
# 10,000 rows/operations). Prints ops/s and latency per operation type, keeps
# results/tikv-<UTC time>.{txt,json}; deletes the benchmark keys afterwards (client cleanup-bench).
SMOKE ?=
THREADS ?= 16
RECORDS ?= $(if $(filter 1,$(SMOKE)),10000,100000)
OPERATIONS ?= $(RECORDS)

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 6
BENCH_MEM ?= 14g
BENCH_SPEC ?= tikv0 tikv1 tikv2 pd0=0.5:512m pd1=0.5:512m pd2=0.5:512m
export BENCH_CLIENT_CPUS ?= 2
export BENCH_NOTE ?= memory raised from 12g to 14g: TiKV sizes its memory usage limit from the VM at startup and was OOM-killed at 2.8g in the TiDB example, so each TiKV gets ~4.2g

benchmark:
	@docker compose ps --status running --services | grep -qx pd0 || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@docker compose --progress quiet --profile bench --profile client build bench client
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=tikv-$$(date -u +%Y%m%dT%H%M%SZ) THREADS=$(THREADS) RECORDS=$(RECORDS) OPERATIONS=$(OPERATIONS) \
		HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T bench; rc=$$?; \
	docker compose run --rm -T client cleanup-bench || rc=1; \
	[ $$rc = 0 ] && docker compose run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	$(PD_CTL) member | $(PD_JQ) '"PD leader: \(.leader.name)", (.members[] | "member \(.name) \(.client_urls[0])")'
	$(PD_CTL) store | $(PD_JQ) '.stores[] | "store \(.store.id) \(.store.address) \(.store.state_name) version=\(.store.version) leaders=\(.status.leader_count) regions=\(.status.region_count) capacity=\(.status.capacity) available=\(.status.available)"'
	$(PD_CTL) region | $(PD_JQ) '"regions: \(.count)", (.regions[] | "region \(.id) [\(.start_key), \(.end_key)) leader=store \(.leader.store_id) peers=\([.peers[].store_id] | map(tostring) | join(","))")'
	$(PD_CTL) config show replication | $(PD_JQ) '"max-replicas: \(."max-replicas")"'

# interactive pd-ctl (try: store, region, region key --format=raw raw/user/001, member, config show)
cli:
	docker exec -it tikv-pd0 /pd-ctl -u http://pd0:2379 -i

down:
	docker compose --profile client --profile bench down --volumes --rmi local
