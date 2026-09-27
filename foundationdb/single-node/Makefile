.PHONY: up init test status cli down

up:
	docker compose up --detach
	$(MAKE) init

# a brand-new cluster has no database until `configure new` is run once;
# ssd-redwood-1 is Redwood, FDB's newer B+tree storage engine (vs the SQLite-based `ssd`)
init:
	until docker exec fdb fdbcli --exec 'status minimal' --timeout 3 >/dev/null 2>&1 || \
		docker exec fdb fdbcli --exec 'configure new single ssd-redwood-1' --timeout 10; do sleep 2; done
	docker exec fdb fdbcli --exec 'status minimal'

# each queries/*.fdbcli file is one fdbcli session, one command per line (# lines are comments)
test:
	docker exec fdb fdbcli --exec 'status' | grep -E 'Redundancy mode|Storage engine'
	for f in queries/*.fdbcli; do echo; echo "-- $$f"; grep '^#' $$f; \
		docker exec fdb fdbcli --exec "$$(grep -v '^#' $$f | grep . | paste -sd ';' -)" || exit 1; done

status:
	docker exec fdb fdbcli --exec 'status'

cli:
	docker exec -it fdb fdbcli

down:
	docker compose down --volumes
