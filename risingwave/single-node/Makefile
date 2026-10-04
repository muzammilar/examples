.PHONY: up test status cli cli-postgres logs down

PSQL := docker compose --profile tools run --rm -T psql -v ON_ERROR_STOP=1 -X
PG   := docker exec -i risingwave-single-postgres psql -U postgres -d sinkdb -X

up:
	docker compose up --detach --wait --wait-timeout 300

# sql/*.sql in order through psql (stops at the first error), then checks that the Postgres
# sink delivered the MV's rows to the `sinkdb` container.
test:
	@for f in sql/*.sql; do \
		echo "-- $$f"; $(PSQL) -e -f /$$f || exit 1; \
	done
	@echo "-- Postgres sink target (sinkdb.revenue_by_region):"
	@for i in $$(seq 1 30); do \
		got=$$($(PG) -tAc "SELECT string_agg(region || '=' || revenue, ',' ORDER BY region) FROM revenue_by_region"); \
		want=$$($(PSQL) -tAc "SELECT string_agg(region || '=' || revenue, ',' ORDER BY region) FROM revenue_by_region"); \
		if [ -n "$$got" ] && [ "$$got" = "$$want" ]; then echo "sink matches MV: $$got"; exit 0; fi; \
		sleep 1; \
	done; echo "sink mismatch: postgres=$$got risingwave=$$want"; exit 1

status:
	$(PSQL) -c "SELECT id, type, state, parallelism FROM rw_worker_nodes ORDER BY id" \
		-c "SELECT name, relation_type, parallelism FROM rw_streaming_parallelism ORDER BY name"

cli:
	docker compose --profile tools run --rm psql

cli-postgres:
	docker exec -it risingwave-single-postgres psql -U postgres -d sinkdb

logs:
	docker logs --tail 50 risingwave-single

down:
	docker compose --profile tools down --volumes --remove-orphans --rmi local
