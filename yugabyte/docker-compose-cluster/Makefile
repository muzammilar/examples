.PHONY: up dashboards test failover benchmark status cli cli-ycql down

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

# ysql/*.sql through ysqlsh, ycql/*.cql through ycqlsh
test:
	for f in ysql/*.sql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(YSQLSH) -f /$$f || exit 1; done
	for f in ycql/*.cql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(YCQLSH) -f /$$f || exit 1; done

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

# ysql_bench TPC-B-like and select-only at each CLIENTS count (split over yb-1..3) for DURATION s
benchmark:
	@for n in yb-1 yb-2 yb-3; do docker compose ps --status running --services | grep -qx $$n || { echo 'run `make up` first'; exit 1; }; done
	@mkdir -p results
	@export NAME=yugabyte-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) CLIENTS="$(CLIENTS)" SCALE=$(SCALE) \
		MAX_TRIES=$(MAX_TRIES) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	{ echo "meta: server_version=$$($(YSQLSH) -Atc 'SELECT version()')"; \
	  echo "meta: tservers_alive=$$($(YB_ADMIN) list_all_tablet_servers | grep -c ALIVE)"; \
	  echo "meta: replication_factor=$$($(YB_ADMIN) get_universe_config | jq '.replicationInfo.liveReplicas.numReplicas')"; } > results/$$NAME.txt; \
	docker compose --progress quiet run --rm -T bench && docker compose --progress quiet run --rm -T bench-report

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
