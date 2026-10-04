.PHONY: up build run status cli cli-postgres logs down

PSQL := docker compose --profile tools run --rm -T psql -X

up:
	docker compose up --detach --wait --wait-timeout 300

build:
	docker compose --profile app build app

# app/main.go: seed SEED_ORDERS orders in Postgres, CDC them into RisingWave, build the MVs, then
# phase 1 (OLTP + events, freshness probes on the MVs) and phase 2 (same OLTP, the same query
# re-run on Postgres every POLL_INTERVAL s); correctness check at the end. Output also in results/.
run:
	@mkdir -p results
	docker compose --profile app run --build --rm -T app | tee results/run-$$(date -u +%Y%m%dT%H%M%SZ).txt

status:
	$(PSQL) -c "SELECT name, relation_type, parallelism FROM rw_streaming_parallelism ORDER BY name" \
		-c "SELECT * FROM rw_cdc_progress" 2>/dev/null || true
	docker exec risingwave-cdc-postgres psql -U postgres -d shop -c "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), confirmed_flush_lsn)) AS lag FROM pg_replication_slots"

cli:
	docker compose --profile tools run --rm psql

cli-postgres:
	docker exec -it risingwave-cdc-postgres psql -U postgres -d shop

logs:
	docker logs --tail 50 risingwave-cdc-rw

down:
	docker compose --profile app --profile tools down --volumes --remove-orphans --rmi local
