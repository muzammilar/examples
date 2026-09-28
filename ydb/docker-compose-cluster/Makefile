.PHONY: up dashboards init test benchmark benchmark-extended benchmark-range failover status cli down

# `make benchmark`: YDB CLI kv + stock workloads; SMOKE=1 for a quick run
SMOKE ?=
BENCH_TIME ?= $(if $(SMOKE),10,60)
BENCH_THREADS ?= $(if $(SMOKE),8,4 16 64)
BENCH_KV_ROWS ?= $(if $(SMOKE),1000,10000)
BENCH_PRODUCTS ?= 100
BENCH_ORDERS ?= $(if $(SMOKE),1000,10000)
BENCH_PARTITIONS ?= 4

# upstream dashboards from YDB's ydb-prometheus Helm chart, at the release tag of our image
YDB_REF := 26.2.1.14
YDB_DASHBOARDS := cpu dboverview dbstatus actors grpc queryengine txproxy datashard

YDB := docker exec -i ydb-storage-1 /ydb
DB := -e grpc://ydb-dynamic-1:2136 -d /Root/testdb
VIEWER := curl -s --max-time 60 localhost:8765/viewer/json

up: dashboards
	docker compose up --detach
	$(MAKE) init

# downloads into the gitignored grafana/provisioning/dashboards/upstream/ (skips existing files)
dashboards:
	grafana/fetch-dashboards.sh grafana/provisioning/dashboards/upstream \
		https://raw.githubusercontent.com/ydb-platform/ydb/$(YDB_REF)/ydb/deploy/helm/ydb-prometheus/dashboards \
		$(YDB_DASHBOARDS:=.json)

# now and then a PDisk fails to open at first start ("Can't open file ... errno# 11", the file is
# still locked), which leaves the cluster DEGRADED or unable to serve /Root/testdb: restart that
# storage node once (checked after bootstrap and again once the database answers)
PDISK_CHECK = @for n in 1 2 3; do \
		docker logs --since "$$(docker inspect -f '{{.State.StartedAt}}' ydb-storage-$$n)" ydb-storage-$$n 2>&1 | \
			grep -q 'BlockDevice initialization error' && { echo "restarting ydb-storage-$$n (PDisk failed to open)"; docker restart ydb-storage-$$n >/dev/null; }; \
	done; true

# One-time: assemble the static storage group, then create /Root/testdb.
# Both steps are retried while the storage nodes finish starting.
init:
	until $(YDB) -y -e grpc://ydb-storage-1:2135 admin cluster bootstrap --uuid ydb-demo 2>/dev/null || \
		$(YDB) -e grpc://ydb-storage-1:2135 -d /Root scheme ls >/dev/null 2>&1; do sleep 5; done
	$(PDISK_CHECK)
	until docker exec ydb-storage-1 /ydbd -s grpc://ydb-storage-1:2135 admin database /Root/testdb status 2>/dev/null | grep -q RUNNING || \
		docker exec ydb-storage-1 /ydbd -s grpc://ydb-storage-1:2135 admin database /Root/testdb create ssd:1; do sleep 5; done
	until $(YDB) $(DB) sql -s 'SELECT 1' >/dev/null 2>&1; do sleep 5; done
	$(PDISK_CHECK)
	@echo "waiting for the self-check to report GOOD"
	@end=$$(( $$(date +%s) + 300 )); \
	until $(VIEWER)/healthcheck | grep -q '"self_check_result":"GOOD"'; do \
		[ $$(date +%s) -lt $$end ] || { echo "not GOOD after 5 min, see http://localhost:8765"; break; }; sleep 5; done
	$(YDB) $(DB) discovery list

# YDB runs DDL and DML as separate queries, hence one file each
test:
	for f in sql/*.sql; do echo "-- $$f"; cat $$f; $(YDB) $(DB) sql -f - < $$f || exit 1; done

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 6
BENCH_MEM ?= 12g
BENCH_SPEC ?= ydb-dynamic-1=1.5:2.25g ydb-dynamic-2=1.5:2.25g ydb-storage-1=1:2.5g ydb-storage-2=1:2.5g ydb-storage-3=1:2.5g
export BENCH_CLIENT_CPUS ?= 0
export BENCH_NOTE ?= no bench container: the ydb CLI load generator runs inside ydb-storage-1 (docker exec) and shares its limits; the range part's Python client runs in its own bench-range container (RANGE_CLIENT_CPUS, default 2)

# init the kv and stock workload tables, run each workload x thread count for
# BENCH_TIME seconds, print txs/s + latency percentiles, clean up; raw + JSON in results/
benchmark:
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; BENCH_TIME='$(BENCH_TIME)' BENCH_THREADS='$(BENCH_THREADS)' BENCH_KV_ROWS='$(BENCH_KV_ROWS)' \
		BENCH_PRODUCTS='$(BENCH_PRODUCTS)' BENCH_ORDERS='$(BENCH_ORDERS)' \
		BENCH_PARTITIONS='$(BENCH_PARTITIONS)' bench/run.sh; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

# longer look under the same budget (bench/extended.sh, ~75 min): thread scaling, kv load while a
# storage and a dynamic node are stopped and started again, tpcc + tpch row vs column, range
# queries (bench/range_bench.py in the bench-range container), per-node load.
# PARTS= picks parts (scaling failover breadth range); SMOKE=1 shortens the range part
PARTS ?=
benchmark-extended:
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; EXT_PARTS='$(or $(PARTS),$(EXT_PARTS))' SMOKE='$(SMOKE)' bench/extended.sh; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

# only the range-query part: PK / secondary-index range scans, a range aggregate, a full scan
benchmark-range:
	@$(MAKE) --no-print-directory benchmark-extended PARTS=range

# stop one storage node (zone-c) and one dynamic node: mirror-3-dc keeps /Root/testdb
# serving through ydb-dynamic-1 while the viewer reports DEGRADED; then bring both back
failover:
	@echo "==> write + read with all nodes up"
	$(YDB) $(DB) sql -f - < failover/01-schema.sql
	$(YDB) $(DB) sql -f - -p '$$phase="all up"' < failover/02-write-read.sql
	@echo "==> stopping ydb-storage-3 (zone-c) and ydb-dynamic-2"
	docker compose stop ydb-storage-3 ydb-dynamic-2
	@echo "==> waiting until the viewer's self-check reports DEGRADED"
	for i in $$(seq 30); do $(VIEWER)/healthcheck | grep -q '"self_check_result":"DEGRADED"' && break; sleep 5; done
	$(VIEWER)/healthcheck | grep -q '"self_check_result":"DEGRADED"'
	$(VIEWER)/cluster | grep -oE '"Overall":"[A-Za-z]+"|"Nodes(Total|Alive)":[0-9]+'
	$(VIEWER)/healthcheck | grep -oE '"(self_check_result|message)":"[^"]*"' | sort | uniq -c
	@echo "==> write + read through ydb-dynamic-1 with one zone and one dynamic node down"
	$(YDB) $(DB) discovery list
	$(YDB) $(DB) sql -f - -p '$$phase="storage-3 + dynamic-2 down"' < failover/02-write-read.sql
	@echo "==> starting ydb-storage-3 and ydb-dynamic-2"
	docker compose start ydb-storage-3 ydb-dynamic-2
	@echo "==> waiting until self-check is GOOD and all 5 nodes are alive (minutes under emulation)"
	end=$$(( $$(date +%s) + 900 )); \
	until $(VIEWER)/healthcheck | grep -q '"self_check_result":"GOOD"' && $(VIEWER)/cluster | grep -q '"NodesAlive":5'; do \
		[ $$(date +%s) -lt $$end ] || { echo "timed out"; exit 1; }; sleep 10; done
	$(VIEWER)/cluster | grep -oE '"Overall":"[A-Za-z]+"|"Nodes(Total|Alive)":[0-9]+'
	$(YDB) $(DB) discovery list
	$(YDB) $(DB) sql -f - -p '$$phase="all up again"' < failover/02-write-read.sql

status:
	curl -s localhost:8765/viewer/json/cluster; echo

cli:
	docker exec -it ydb-storage-1 /ydb $(DB) sql

down:
	docker compose --profile bench down --volumes
