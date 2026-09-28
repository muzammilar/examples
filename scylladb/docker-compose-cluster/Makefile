.PHONY: up dashboards test failover benchmark status cli down

# upstream dashboards (prebuilt for ScyllaDB $(SCYLLA_DASH_VERSION)) and the recording rules they rely on
SCYLLA_MONITORING_REF := 4.16.1
SCYLLA_DASH_VERSION := 2026.1
SCYLLA_MONITORING := https://raw.githubusercontent.com/scylladb/scylla-monitoring/$(SCYLLA_MONITORING_REF)

# make benchmark: seconds per workload, cassandra-stress threads, partitions written (SMOKE=1: a short run)
SMOKE ?=
DURATION ?= $(if $(SMOKE),10,60)
THREADS ?= 32
KEYS ?= $(if $(SMOKE),100000,1000000)
LWT_KEYS ?= 10000

up: dashboards
	docker compose up --detach --wait
	docker exec scylla-1 nodetool status

# downloads into the gitignored grafana/provisioning/dashboards/upstream/ and prometheus/upstream/
dashboards:
	grafana/fetch-dashboards.sh grafana/provisioning/dashboards/upstream $(SCYLLA_MONITORING)/grafana/build/ver_$(SCYLLA_DASH_VERSION) \
		$(foreach d,overview detailed cql,scylla-$(d).$(SCYLLA_DASH_VERSION).json)
	grafana/fetch-dashboards.sh prometheus/upstream $(SCYLLA_MONITORING)/prometheus/prom_rules prometheus.latency.rules.yml

# RF=3 keyspace, a few writes/reads at QUORUM
test:
	docker exec scylla-1 cqlsh --file /cql/test.cql

# stop scylla-3: QUORUM and LWT keep working, CONSISTENCY ALL does not; then bring it back
failover:
	@echo "==> stopping scylla-3"
	docker compose stop scylla-3
	@echo "==> waiting until scylla-1 sees it down (DN)"
	for i in $$(seq 30); do docker exec scylla-1 nodetool status | grep -q '^DN' && break; sleep 2; done
	docker exec scylla-1 nodetool status | grep -E '^(UN|DN)'
	docker exec scylla-1 nodetool status | grep -q '^DN'
	@echo "==> QUORUM write + read (2 of 3 replicas)"
	docker exec -i scylla-1 cqlsh < failover/quorum.cql
	@echo "==> CONSISTENCY ALL (3 of 3 replicas)"
	@if docker exec -i scylla-1 cqlsh < failover/all.cql; then \
		echo "unexpected: ALL succeeded with a node down"; exit 1; \
	else \
		echo "expected: Unavailable, only 2 of 3 replicas alive"; \
	fi
	@echo "==> lightweight transactions (Paxos quorum)"
	docker exec -i scylla-1 cqlsh < failover/lwt.cql
	@echo "==> starting scylla-3"
	docker compose start scylla-3
	@echo "==> waiting until all three nodes are UN"
	for i in $$(seq 90); do [ "$$(docker exec scylla-1 nodetool status | grep -c '^UN')" = 3 ] && break; sleep 2; done
	docker exec scylla-1 nodetool status | grep -E '^(UN|DN)'
	[ "$$(docker exec scylla-1 nodetool status | grep -c '^UN')" = 3 ]
	@echo "==> CONSISTENCY ALL works again: all 3 replicas answer again (an ALL read also read-repairs)"
	docker exec -i scylla-1 cqlsh < failover/all.cql

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 6
BENCH_MEM ?= 12g
BENCH_SPEC ?= scylla-1 scylla-2 scylla-3
export BENCH_CLIENT_CPUS ?= 2

# cassandra-stress at QUORUM: write, read, 50/50 mixed, plain vs LWT update, DURATION s each;
# prints a summary table, keeps results/scylladb-<UTC time>.{txt,json}
benchmark:
	@docker compose ps --status running --services | grep -qx scylla-3 || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@echo "==> dropping the benchmark keyspaces of an earlier run"
	@docker exec scylla-1 cqlsh -e 'DROP KEYSPACE IF EXISTS keyspace1; DROP KEYSPACE IF EXISTS bench_lwt'
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=scylladb-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) THREADS=$(THREADS) KEYS=$(KEYS) LWT_KEYS=$(LWT_KEYS) \
		HOST_INFO="$$(uname -sm)" DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	{ echo "meta: server_version=$$(docker exec scylla-1 scylla --version)"; \
	  echo "meta: nodes_up=$$(docker exec scylla-1 nodetool status | grep -c '^UN')"; \
	  echo "meta: node_args=$$(docker inspect scylla-1 --format '{{join .Config.Cmd " "}}')"; } > results/$$NAME.txt; \
	docker compose run --rm -T bench && docker compose run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker exec scylla-1 nodetool status

cli:
	docker exec -it scylla-1 cqlsh

down:
	docker compose --profile bench down --volumes
