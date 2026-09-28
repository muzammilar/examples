.PHONY: up dashboards init wait-healthy test failover status cli benchmark down

# upstream dashboard from the exporter repo (the commit behind image 3.1.0 plus its dashboard fix)
FDB_EXPORTER_REF := b8e124de1fd83b6467bafedb6ad438e48eaaed31

# `make benchmark` knobs (empty = bench.py defaults; SMOKE=1 for a short run)
SMOKE ?=
DURATION ?=
CLIENTS ?=
ROWS ?=
HOT ?=

FDBCLI := docker exec fdb-0 fdbcli

up: dashboards
	docker compose up --detach
	$(MAKE) init

# downloads into the gitignored grafana/provisioning/dashboards/upstream/ (skips existing files)
dashboards:
	grafana/fetch-dashboards.sh grafana/provisioning/dashboards/upstream \
		https://raw.githubusercontent.com/aikoven/foundationdb-exporter/$(FDB_EXPORTER_REF) grafana/foundationdb.json

# a brand-new cluster has no database until `configure new` is run once;
# ssd-redwood-1 is Redwood, FDB's newer B+tree storage engine (vs the SQLite-based `ssd`)
init:
	until $(FDBCLI) --exec 'status minimal' --timeout 3 >/dev/null 2>&1 || \
		$(FDBCLI) --exec 'configure new double ssd-redwood-1' --timeout 10; do sleep 2; done
	@$(MAKE) --no-print-directory wait-healthy
	$(FDBCLI) --exec 'status minimal'

# up to 120 x 2 s until data distribution reports every shard fully replicated
# (jq ships in the foundationdb image, so nothing is needed on the host)
wait-healthy:
	@echo "waiting for .cluster.data.state.healthy"
	@for i in $$(seq 120); do \
		docker exec fdb-0 sh -c "fdbcli --exec 'status json' --timeout 5 | jq -e '.cluster.data.state.healthy == true'" >/dev/null 2>&1 && exit 0; \
		sleep 2; done; echo 'cluster not healthy after 240 s'; $(FDBCLI) --exec 'status'; exit 1

# one fdbcli session over queries/test.fdbcli, one command per line (# lines are comments)
test:
	$(FDBCLI) --exec 'status' | grep -E 'Redundancy mode|Storage engine|Fault Tolerance'
	$(FDBCLI) --exec "$$(grep -v '^#' queries/test.fdbcli | grep . | paste -sd ';' -)"

# stop one of three processes: commits continue (2 of 3 coordinators, the other replica of
# each key), status shows the lost fault tolerance; then restart it and wait for healing
failover:
	docker stop fdb-2
	@echo; echo "-- fdb-2 stopped: write + read in one transaction"
	$(FDBCLI) --timeout 60 --exec 'writemode on; begin; set failover/k written-while-fdb-2-down; get failover/k; commit; get failover/k'
	@echo; echo "-- status with fdb-2 down"
	$(FDBCLI) --exec 'status' | grep -E 'Machines|Coordinators|Fault Tolerance|Replication health|Moving data|Recovery state|Healthy|Unhealthy|unreachable' || true
	docker start fdb-2
	@$(MAKE) --no-print-directory wait-healthy
	@echo; echo "-- fdb-2 back"
	$(FDBCLI) --exec 'status' | grep -E 'Machines|Fault Tolerance|Replication health'
	$(FDBCLI) --exec 'get failover/k'

status:
	$(FDBCLI) --exec 'status'

cli:
	docker exec -it fdb-0 fdbcli

# needs `make up`; SMOKE=1 for a 5 s/workload run, or set DURATION / CLIENTS / ROWS / HOT
benchmark:
	@docker compose ps --status running --services | grep -qx fdb-0 || { echo 'run `make up` first'; exit 1; }
	SMOKE=$(SMOKE) DURATION=$(DURATION) CLIENTS=$(CLIENTS) ROWS=$(ROWS) HOT=$(HOT) \
		docker compose run --build --rm bench

# keeps the uv-cache volume (bench profile); removes the locally built bench image
down:
	docker compose down --volumes
	docker compose --profile bench down --rmi local
