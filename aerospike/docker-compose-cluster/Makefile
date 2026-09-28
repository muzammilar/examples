.PHONY: up dashboards test failover benchmark status cli down

# upstream dashboards from aerospike-monitoring (release tag)
AEROSPIKE_MONITORING_REF := v3.20.0

# make benchmark: seconds per timed workload, asbench threads, keys inserted (SMOKE=1: a short run)
SMOKE ?=
DURATION ?= $(if $(SMOKE),10,60)
THREADS ?= 16
KEYS ?= $(if $(SMOKE),100000,1000000)

# aql exits 0 even on errors: print its output, fail on an "Error" line or when $(1) is missing
AQL := docker compose run --rm -T tools aql -h aerospike-1 -f /dev/stdin
AQL_EXPECT = 2>&1 | awk -v need='$(1)' '{print} /^Error/{e=1} index($$0, need){ok=1} END{exit e || !ok}'

up: dashboards
	docker compose up --detach
	@for i in $$(seq 120); do \
		docker exec aerospike-1 asinfo -v 'cluster-stable:size=3' 2>/dev/null | grep -qv ERROR && { echo "cluster stable, size 3"; exit 0; }; \
		sleep 1; \
	done; echo "cluster not stable at size 3 after 120 s (docker compose logs aerospike-1)"; exit 1

# downloads into the gitignored grafana/provisioning/dashboards/upstream/ (skips existing files)
dashboards:
	grafana/fetch-dashboards.sh grafana/provisioning/dashboards/upstream \
		https://raw.githubusercontent.com/aerospike/aerospike-monitoring/$(AEROSPIKE_MONITORING_REF)/config/grafana/dashboards \
		cluster.json node.json namespace.json set.json latency.json

# writes, reads and deletes a few records in namespace test (RF=2), set demo
test:
	docker compose run --rm tools aql -h aerospike-1 -f /aql/test.aql

# stop aerospike-3: replicas get promoted, every record stays readable and writable; then bring it back
failover:
	@echo "==> emptying set test.failover (left over from an earlier run)"
	docker exec aerospike-1 asinfo -v 'truncate:namespace=test;set=failover'
	@# truncate is applied cluster-wide in the background: wait until no node holds a record of the set
	@for i in $$(seq 30); do \
		n=0; for c in aerospike-1 aerospike-2 aerospike-3; do \
			o=$$(docker exec $$c asinfo -v 'sets/test/failover' | tr ':;' '\n\n' | sed -n 's/^objects=//p'); \
			n=$$((n + $${o:-0})); \
		done; \
		[ $$n = 0 ] && exit 0; sleep 1; \
	done; echo "set test.failover still holds $$n records after 30 s"; exit 1
	@echo "==> writing 10 records (all three nodes up)"
	$(AQL) < failover/write-before.aql $(call AQL_EXPECT,record affected)
	docker exec aerospike-1 asadm -e 'info namespace object'
	@echo "==> stopping aerospike-3"
	docker compose stop aerospike-3
	@echo "==> waiting until the cluster re-forms at size 2"
	for i in $$(seq 30); do docker exec aerospike-1 asinfo -v 'cluster-stable:size=2' | grep -qv ERROR && break; sleep 1; done
	docker exec aerospike-1 asinfo -v statistics -l | grep -E '^cluster_size=2$$'
	docker exec aerospike-1 asadm -e 'info network'
	@echo "==> reading all 10 records (the surviving replicas are now masters)"
	$(AQL) < failover/read.aql $(call AQL_EXPECT,10 rows in set)
	@echo "==> writing 5 more records with one node down"
	$(AQL) < failover/write-during.aql $(call AQL_EXPECT,record affected)
	$(AQL) < failover/read.aql $(call AQL_EXPECT,15 rows in set)
	@echo "==> starting aerospike-3"
	docker compose start aerospike-3
	@echo "==> waiting for size 3 and for migrations to finish"
	for i in $$(seq 60); do docker exec aerospike-1 asinfo -v 'cluster-stable:size=3;ignore-migrations=false' | grep -qv ERROR && break; sleep 1; done
	docker exec aerospike-1 asinfo -v 'cluster-stable:size=3;ignore-migrations=false' | grep -v ERROR
	docker exec aerospike-1 asadm -e 'info namespace object'
	$(AQL) < failover/read.aql $(call AQL_EXPECT,15 rows in set)

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 6
BENCH_MEM ?= 12g
BENCH_SPEC ?= aerospike-1 aerospike-2 aerospike-3
export BENCH_CLIENT_CPUS ?= 2

# asbench (aerospike-tools): insert KEYS records, then DURATION s read-only and 80/20 read/update;
# prints a summary table, keeps results/aerospike-<UTC time>.{txt,json}
benchmark:
	@docker compose ps --status running --services | grep -qx aerospike-1 || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=aerospike-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) THREADS=$(THREADS) KEYS=$(KEYS) \
		HOST_INFO="$$(uname -sm)" DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T bench && docker compose run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker exec aerospike-1 asadm -e 'info'

cli:
	docker exec -it aerospike-1 asadm

down:
	docker compose --profile tools --profile bench down --volumes
