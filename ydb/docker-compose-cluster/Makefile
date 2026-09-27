.PHONY: up dashboards init test failover status cli down

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

# One-time: assemble the static storage group, then create /Root/testdb.
# Both steps are retried while the storage nodes finish starting.
init:
	until $(YDB) -y -e grpc://ydb-storage-1:2135 admin cluster bootstrap --uuid ydb-demo 2>/dev/null || \
		$(YDB) -e grpc://ydb-storage-1:2135 -d /Root scheme ls >/dev/null 2>&1; do sleep 5; done
	until docker exec ydb-storage-1 /ydbd -s grpc://ydb-storage-1:2135 admin database /Root/testdb status 2>/dev/null | grep -q RUNNING || \
		docker exec ydb-storage-1 /ydbd -s grpc://ydb-storage-1:2135 admin database /Root/testdb create ssd:1; do sleep 5; done
	until $(YDB) $(DB) sql -s 'SELECT 1' >/dev/null 2>&1; do sleep 5; done
	$(YDB) $(DB) discovery list

# YDB runs DDL and DML as separate queries, hence one file each
test:
	for f in sql/*.sql; do echo "-- $$f"; cat $$f; $(YDB) $(DB) sql -f - < $$f || exit 1; done

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
	docker compose down --volumes
