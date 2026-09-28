.PHONY: up test status cli cli-ycql down

YSQLSH := docker exec -i yugabyte ysqlsh -h yugabyte -v ON_ERROR_STOP=1
YCQLSH := docker exec -i yugabyte ycqlsh yugabyte

up:
	docker compose up --detach --wait
	docker exec yugabyte yugabyted status

# ysql/*.sql through ysqlsh, then ycql/*.cql through ycqlsh
test:
	for f in ysql/*.sql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(YSQLSH) -f /$$f || exit 1; done
	for f in ycql/*.cql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(YCQLSH) -f /$$f || exit 1; done

status:
	docker exec yugabyte yugabyted status

cli:
	docker exec -it yugabyte ysqlsh -h yugabyte

cli-ycql:
	docker exec -it yugabyte ycqlsh yugabyte

down:
	docker compose down --volumes
