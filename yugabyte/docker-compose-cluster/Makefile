.PHONY: up dashboards test failover benchmark benchmark-ycql status cli cli-ycql down

YUGABYTED := docker exec yb-1 yugabyted
YB_ADMIN := docker exec yb-1 yb-admin --master_addresses yb-1:7100,yb-2:7100,yb-3:7100
YSQLSH := docker exec -i yb-1 ysqlsh -h yb-1 -v ON_ERROR_STOP=1
YCQLSH := docker exec -i yb-1 ycqlsh yb-1

# make benchmark: seconds per ysql_bench run, client counts, scale (x 100k accounts),
# tries per transaction on serialization/conflict errors (SMOKE=1: a short run)
SMOKE ?=
DURATION ?= $(if $(SMOKE),10,60)
CLIENTS ?= 1 8 16
SCALE ?= $(if $(SMOKE),2,10)
MAX_TRIES ?= 10

# make benchmark-ycql: seconds per timed run, thread counts, keys loaded first, value bytes, threads
# for the (untimed) load; DURATION/SMOKE as above. yb-sample-apps release and its jar's sha256.
THREADS ?= 1 8 32
KEYS ?= $(if $(SMOKE),100000,1000000)
VALUE_SIZE ?= 100
LOAD_THREADS ?= 32
SAMPLE_APPS_VERSION := v1.4.3
SAMPLE_APPS_SHA256 := 24dc7a70b38bfb92ff70fa02b78699fb043acd716a4e5bd22f5ec02ee4eda306

# upstream dashboard (cloud/grafana) from the yugabyte-db release tag matching the image
YUGABYTE_DB_REF := v2026.1.2.0

# After all three nodes joined: place one replica of every tablet in each zone.
up: dashboards
	docker compose up --detach --wait
	$(YUGABYTED) configure data_placement --fault_tolerance=zone
	$(YUGABYTED) status

# downloads into the gitignored grafana/provisioning/dashboards/upstream/ (skips existing files)
dashboards:
	grafana/fetch-dashboards.sh grafana/provisioning/dashboards/upstream \
		https://raw.githubusercontent.com/yugabyte/yugabyte-db/$(YUGABYTE_DB_REF)/cloud/grafana YugabyteDB.json

# ysql/*.sql through ysqlsh, ycql/*.cql through ycqlsh (after ycql/schema/, whose new indexes get
# 3 s to finish backfilling); each ycql/errors/*.cql must fail with the error in its "-- expect:"
# line; ycql/ttl/ writes rows with TTLs and reads them again 6 s later
test:
	for f in ysql/*.sql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(YSQLSH) -f /$$f || exit 1; done
	for f in ycql/schema/*.cql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(YCQLSH) -f /$$f || exit 1; done
	sleep 3
	for f in ycql/*.cql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(YCQLSH) -f /$$f || exit 1; done
	for f in ycql/errors/*.cql; do echo "-- $$f"; cat $$f; echo "-- output:"; \
		out=$$($(YCQLSH) -f /$$f 2>&1); echo "$$out"; \
		echo "$$out" | grep -qF "$$(sed -n 's/^-- expect: //p' $$f)" || { echo "expected that error"; exit 1; }; done
	$(YCQLSH) -e 'SELECT * FROM demo.customers'
	echo "-- ycql/ttl/write.cql"; cat ycql/ttl/write.cql; echo "-- output:"; $(YCQLSH) -f /ycql/ttl/write.cql || exit 1
	sleep 6
	echo "-- ycql/ttl/read.cql"; cat ycql/ttl/read.cql; echo "-- output:"; \
		out=$$($(YCQLSH) -f /ycql/ttl/read.cql 2>&1) || { echo "$$out"; exit 1; }; echo "$$out"; \
		echo "$$out" | grep -Eq '^ +s2 +\| +bob +\| +null' || { echo "expected s2 without its secret"; exit 1; }; \
		echo "$$out" | grep -Eq '^ +0$$' || { echo "expected s1 to be gone"; exit 1; }

# stop yb-3: after tablet leaders re-elect on yb-1/yb-2, YSQL and YCQL keep working; then bring it back
failover:
	@echo "==> baseline YSQL + YCQL writes (all three nodes up)"
	$(YSQLSH) -f /failover/setup.sql
	$(YCQLSH) -f /failover/setup.cql
	@echo "==> stopping yb-3"
	docker compose stop yb-3
	@echo "==> waiting for tablet leader re-election (YSQL read succeeds)"
	for i in $$(seq 30); do $(YSQLSH) -c 'SELECT count(*) FROM failover' >/dev/null 2>&1 && break; echo "not yet, retrying"; sleep 2; done
	@echo "==> YSQL distributed transaction + read"
	$(YSQLSH) -f /failover/write-read.sql
	@echo "==> YCQL write + read"
	$(YCQLSH) -f /failover/write-read.cql
	@echo "==> yb-3's master is unreachable, its tserver stops heartbeating"
	$(YB_ADMIN) list_all_masters
	$(YB_ADMIN) list_all_tablet_servers
	@echo "==> starting yb-3"
	docker compose start yb-3
	@echo "==> waiting until yb-3 is healthy and its master and tserver are back"
	for i in $$(seq 60); do [ "$$(docker inspect -f '{{.State.Health.Status}}' yb-3)" = healthy ] && break; sleep 2; done
	[ "$$(docker inspect -f '{{.State.Health.Status}}' yb-3)" = healthy ]
	for i in $$(seq 30); do [ "$$($(YB_ADMIN) list_all_masters | grep -c ALIVE)" = 3 ] && break; sleep 2; done
	$(YB_ADMIN) list_all_masters
	$(YB_ADMIN) list_all_masters | grep -Eq 'yb-3:7100[[:space:]]+ALIVE'
	$(YB_ADMIN) list_all_tablet_servers
	$(YB_ADMIN) list_all_tablet_servers | grep -Eq 'yb-3:9100[[:space:]].*ALIVE'
	$(YSQLSH) -c 'SELECT * FROM failover ORDER BY phase'
	$(YCQLSH) -e 'SELECT * FROM demo.failover'

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 6
BENCH_MEM ?= 12g
BENCH_SPEC ?= yb-1 yb-2 yb-3
export BENCH_CLIENT_CPUS ?= 2

# ysql_bench TPC-B-like and select-only at each CLIENTS count (split over yb-1..3) for DURATION s
benchmark:
	@for n in yb-1 yb-2 yb-3; do docker compose ps --status running --services | grep -qx $$n || { echo 'run `make up` first'; exit 1; }; done
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=yugabyte-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) CLIENTS="$(CLIENTS)" SCALE=$(SCALE) \
		MAX_TRIES=$(MAX_TRIES) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	{ echo "meta: server_version=$$($(YSQLSH) -Atc 'SELECT version()')"; \
	  echo "meta: tservers_alive=$$($(YB_ADMIN) list_all_tablet_servers | grep -c ALIVE)"; \
	  echo "meta: replication_factor=$$($(YB_ADMIN) get_universe_config | jq '.replicationInfo.liveReplicas.numReplicas')"; } > results/$$NAME.txt; \
	docker compose --progress quiet run --rm -T bench && docker compose --progress quiet run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

# yb-sample-apps (YugabyteDB's workload generator), CassandraKeyValue over YCQL: load KEYS keys, then
# DURATION s each of updates, reads and 50/50 at each THREADS count; same budget as `make benchmark`
bench/yb-sample-apps.jar:
	curl -fsSL -o $@.tmp https://github.com/yugabyte/yb-sample-apps/releases/download/$(SAMPLE_APPS_VERSION)/yb-sample-apps.jar
	@sum=$$( (sha256sum $@.tmp 2>/dev/null || shasum -a 256 $@.tmp) | cut -d' ' -f1); \
	[ "$$sum" = $(SAMPLE_APPS_SHA256) ] || { echo "$@: sha256 $$sum, expected $(SAMPLE_APPS_SHA256)"; rm -f $@.tmp; exit 1; }
	mv $@.tmp $@

benchmark-ycql: bench/yb-sample-apps.jar
	@for n in yb-1 yb-2 yb-3; do docker compose ps --status running --services | grep -qx $$n || { echo 'run `make up` first'; exit 1; }; done
	@mkdir -p results
	@echo "==> dropping the table of an earlier run"
	@$(YCQLSH) -e 'CREATE KEYSPACE IF NOT EXISTS ybdemo_keyspace; DROP TABLE IF EXISTS ybdemo_keyspace.cassandrakeyvalue'
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=yugabyte-ycql-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) THREADS="$(THREADS)" \
		KEYS=$(KEYS) VALUE_SIZE=$(VALUE_SIZE) LOAD_THREADS=$(LOAD_THREADS) SAMPLE_APPS_VERSION=$(SAMPLE_APPS_VERSION) \
		HOST_INFO="$$(uname -sm)" DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	{ echo "meta: server_version=$$($(YSQLSH) -Atc 'SELECT version()')"; \
	  echo "meta: tservers_alive=$$($(YB_ADMIN) list_all_tablet_servers | grep -c ALIVE)"; \
	  echo "meta: replication_factor=$$($(YB_ADMIN) get_universe_config | jq '.replicationInfo.liveReplicas.numReplicas')"; } > results/$$NAME.txt; \
	docker compose --progress quiet run --rm -T bench-ycql && docker compose --progress quiet run --rm -T bench-ycql-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	$(YUGABYTED) status
	$(YB_ADMIN) list_all_masters
	$(YB_ADMIN) list_all_tablet_servers

cli:
	docker exec -it yb-1 ysqlsh -h yb-1

cli-ycql:
	docker exec -it yb-1 ycqlsh yb-1

down:
	docker compose --profile bench down --volumes
