.PHONY: up test failover status cli cli-ycql down

YUGABYTED := docker exec yb-1 yugabyted
YB_ADMIN := docker exec yb-1 yb-admin --master_addresses yb-1:7100,yb-2:7100,yb-3:7100
YSQLSH := docker exec -i yb-1 ysqlsh -h yb-1 -v ON_ERROR_STOP=1
YCQLSH := docker exec -i yb-1 ycqlsh yb-1

# After all three nodes joined: place one replica of every tablet in each zone.
up:
	docker compose up --detach --wait
	$(YUGABYTED) configure data_placement --fault_tolerance=zone
	$(YUGABYTED) status

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

status:
	$(YUGABYTED) status
	$(YB_ADMIN) list_all_masters
	$(YB_ADMIN) list_all_tablet_servers

cli:
	docker exec -it yb-1 ysqlsh -h yb-1

cli-ycql:
	docker exec -it yb-1 ycqlsh yb-1

down:
	docker compose down --volumes
