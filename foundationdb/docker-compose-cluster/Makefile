.PHONY: up dashboards init test status cli down

# upstream dashboard from the exporter repo (the commit behind image 3.1.0 plus its dashboard fix)
FDB_EXPORTER_REF := b8e124de1fd83b6467bafedb6ad438e48eaaed31

up: dashboards
	docker compose up --detach
	$(MAKE) init

# downloads into the gitignored grafana/provisioning/dashboards/upstream/ (skips existing files)
dashboards:
	grafana/fetch-dashboards.sh grafana/provisioning/dashboards/upstream \
		https://raw.githubusercontent.com/aikoven/foundationdb-exporter/$(FDB_EXPORTER_REF) grafana/foundationdb.json

# a brand-new cluster has no database until `configure new` is run once
init:
	until docker exec fdb-0 fdbcli --exec 'status minimal' --timeout 3 >/dev/null 2>&1 || \
		docker exec fdb-0 fdbcli --exec 'configure new double ssd' --timeout 10; do sleep 2; done
	docker exec fdb-0 fdbcli --exec 'status minimal'

# every line of queries/test.fdbcli is run as one fdbcli command
test:
	docker exec fdb-0 fdbcli --exec "$$(paste -sd ';' queries/test.fdbcli)"

status:
	docker exec fdb-0 fdbcli --exec 'status'

cli:
	docker exec -it fdb-0 fdbcli

down:
	docker compose down --volumes
