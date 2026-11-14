COMPOSE := docker compose
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
BULK ?= 1
POOL_SIZE ?= 16
BURST_CLIENTS ?= 256
BURST_REQUESTS ?= 4000
RATE ?= 0
CHAOS_RATE ?= 25000
FAULT_SHARD ?= 0
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)
RUN_ID := $(RUN_ID)
RESULTS_DIR := results/$(RUN_ID)
export COUNT CUSTOMERS WORKERS BATCH BULK RATE POOL_SIZE FAULT_SHARD RUN_ID RESULTS_DIR
.NOTPARALLEL:

.PHONY: docker-up up test failover chaos-replica-lag status cli down docker-down clean-results reset

docker-up: render-config
	$(COMPOSE) up --build -d --wait --wait-timeout 240

up:
	sh scripts/provision.sh
	$(COMPOSE) build workload
	$(COMPOSE) run --rm -T workload seed

clean-results:
	@mkdir -p $(RESULTS_DIR); chmod 777 $(RESULTS_DIR)
	@test ! -f $(RESULTS_DIR)/acknowledged.jsonl || { echo "Choose a fresh RUN_ID" >&2; exit 1; }
	@sh scripts/phase.sh baseline

# Full million-record load, with a durable client witness outside the databases.
reset:
	@set -eu; for shard in 0; do \
	  node=$$(sh scripts/primary.sh $$shard); \
	  $(COMPOSE) exec -T $$node psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'TRUNCATE orders'; \
	done

test: images clean-results reset
	$(COMPOSE) run --rm -T workload load
	$(COMPOSE) run --rm -T workload check
	$(MAKE) connection-burst
	sh scripts/replicas.sh
	sh scripts/placement.sh
	$(MAKE) status

failover: RATE = $(CHAOS_RATE)
failover: images clean-results reset
	@fault_status=0; sh scripts/run-fault.sh primary || fault_status=$$?; \
	$(COMPOSE) run --rm -T workload check || exit 1; \
	[ $$fault_status -eq 0 ]
	sh scripts/replicas.sh
	sh scripts/placement.sh
	@cat $(RESULTS_DIR)/load.json $(RESULTS_DIR)/failover.json
	$(MAKE) status

chaos-replica-lag: RATE = $(CHAOS_RATE)
chaos-replica-lag: images clean-results reset
	@fault_status=0; sh scripts/run-fault.sh replica-lag || fault_status=$$?; \
	$(COMPOSE) run --rm -T workload check || exit 1; \
	[ $$fault_status -eq 0 ]
	sh scripts/replicas.sh
	sh scripts/placement.sh
	@cat $(RESULTS_DIR)/load.json $(RESULTS_DIR)/failover.json

status:
	$(COMPOSE) ps
	@set -eu; for shard in 0; do \
	  node=$$(sh scripts/primary.sh $$shard); echo "shard=$$shard primary=$$node"; \
	  $(COMPOSE) exec -T $$node psql -U postgres -d postgres -c 'SELECT count(*) AS orders, count(DISTINCT customer_id) AS customers, sum(amount) AS amount FROM orders' -c 'SELECT application_name, sync_state, write_lag, flush_lag, replay_lag FROM pg_stat_replication'; \
	done
	$(COMPOSE) run --rm -T -e PGPASSWORD=local-admin client psql -h pgdog -p 6432 -U admin -d admin -c 'SHOW REPLICATION'

cli:
	$(COMPOSE) run --rm client psql -h pgdog -p 6432 -U postgres -d orders

down:
	@set -eu; for shard in 0; do \
	  node=$$(sh scripts/primary.sh $$shard); \
	  $(COMPOSE) exec -T $$node psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c 'DROP TABLE IF EXISTS orders, customers'; \
	done

docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local

.PHONY: images
images:
	$(COMPOSE) build workload

.PHONY: render-config pool-size config replica-reads compare-pools
render-config:
	python3 scripts/render-config.py

pool-size: render-config
	$(COMPOSE) run --rm -T -e PGPASSWORD=local-admin client psql -h pgdog -p 6432 -U admin -d admin -v ON_ERROR_STOP=1 -c RELOAD

config:
	$(COMPOSE) run --rm -T -e PGPASSWORD=local-admin client psql -h pgdog -p 6432 -U admin -d admin -c 'SHOW CONFIG' -c 'SHOW POOLS' -c 'SHOW REPLICATION'

replica-reads:
	@result=$$($(COMPOSE) run --rm -T client psql -h pgdog -p 6432 -U postgres -d orders -Atq -c "SET pgdog.role='replica'" -c 'SELECT pg_is_in_recovery()'); \
	[ "$$result" = t ] || { echo "Expected a replica read; got $$result" >&2;exit 1; }

compare-pools:
	$(MAKE) pool-size POOL_SIZE=4
	$(MAKE) test WORKERS=64 RUN_ID=$(RUN_ID)-pool4
	$(MAKE) pool-size POOL_SIZE=16
	$(MAKE) test WORKERS=64 RUN_ID=$(RUN_ID)-pool16

.PHONY: connection-burst
connection-burst: images
	@mkdir -p $(RESULTS_DIR); chmod 777 $(RESULTS_DIR)
	@node=$$(sh scripts/primary.sh 0); $(COMPOSE) run --rm -T -e DIRECT_DSN="postgres://postgres:local-postgres@$$node:5432/postgres?sslmode=disable" -e BURST_CLIENTS=$(BURST_CLIENTS) -e BURST_REQUESTS=$(BURST_REQUESTS) workload burst
