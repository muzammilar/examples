.PHONY: up dashboards init test status cli down

# upstream dashboards from YDB's ydb-prometheus Helm chart, at the release tag of our image
YDB_REF := 26.2.1.14
YDB_DASHBOARDS := cpu dboverview dbstatus actors grpc queryengine txproxy datashard

YDB := docker exec -i ydb-storage-1 /ydb
DB := -e grpc://ydb-dynamic-1:2136 -d /Root/testdb

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

status:
	curl -s localhost:8765/viewer/json/cluster; echo

cli:
	docker exec -it ydb-storage-1 /ydb $(DB) sql

down:
	docker compose down --volumes
