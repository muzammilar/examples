.PHONY: up test status cli cli-ycql down

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
