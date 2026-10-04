.PHONY: up test status cli failover scale-out scale-in logs down

PSQL := docker compose --profile tools run --rm -T psql -v ON_ERROR_STOP=1 -X

# postgres + minio (+ bucket) -> meta -> compute-1/2 + compactor -> frontend, each waited on by
# its healthcheck.
up:
	docker compose up --detach --wait --wait-timeout 300

test:
	@for f in sql/0*.sql; do echo "-- $$f"; $(PSQL) -e -f /$$f || exit 1; done

status:
	@docker compose --profile scale ps --format 'table {{.Name}}\t{{.Status}}'
	$(PSQL) -c "SELECT id, host, type, state, parallelism FROM rw_worker_nodes ORDER BY id"

cli:
	docker compose --profile tools run --rm psql

# SIGKILL compute-2 under load (NODE, KILL_AT, DOWN, DURATION override); see scripts/failover.sh
failover:
	scripts/failover.sh

# 2 -> 4 compute nodes and back under load (SAMPLE seconds per throughput sample); scripts/scale.sh
scale-out:
	scripts/scale.sh out

scale-in:
	scripts/scale.sh in

logs:
	docker compose logs --tail 20 meta compute-1 compute-2

down:
	docker compose --profile tools --profile scale down --volumes --remove-orphans --rmi local
