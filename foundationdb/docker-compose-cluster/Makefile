.PHONY: up init test status cli down

up:
	docker compose up --detach
	$(MAKE) init

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
