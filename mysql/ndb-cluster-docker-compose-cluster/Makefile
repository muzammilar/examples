SUBSCRIBER_LOOKUPS ?= 10000
SUBSCRIBER_SCANS ?= 20
SUBSCRIBER_OPERATIONS ?= 10000
export SUBSCRIBER_LOOKUPS SUBSCRIBER_SCANS SUBSCRIBER_OPERATIONS
COMPOSE := docker compose
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
BULK ?= 1
RATE ?= 0
CHAOS_RATE ?= 25000
FAULT_NODE ?= data1
FAULT_KIND ?= data
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)
RUN_ID := $(RUN_ID)
RESULTS_DIR := results/$(RUN_ID)
export COUNT CUSTOMERS WORKERS BATCH BULK RATE FAULT_NODE FAULT_KIND RUN_ID RESULTS_DIR
.NOTPARALLEL:
.PHONY: docker-up up images test reset clean-results failover chaos-sql chaos-management status cli down docker-down

docker-up:
	$(COMPOSE) up -d --wait --wait-timeout 360

up:
	sh scripts/sql.sh sql0 < sql/schema.sql
	$(MAKE) images
	$(COMPOSE) run --rm -T workload seed

images:
	$(COMPOSE) build workload

clean-results:
	@mkdir -p $(RESULTS_DIR); chmod 777 $(RESULTS_DIR)
	@test ! -f $(RESULTS_DIR)/acknowledged.jsonl || { echo 'Use a fresh RUN_ID' >&2;exit 1; }
	sh scripts/phase.sh baseline

reset:
	sh scripts/sql.sh sql0 -e 'TRUNCATE TABLE orders'

test: images clean-results reset
	$(COMPOSE) run --rm -T workload load
	$(COMPOSE) run --rm -T workload check
	$(COMPOSE) run --rm -T workload subscribers
	sh scripts/check-cluster.sh

failover: RATE = $(CHAOS_RATE)
failover: images clean-results reset
	@fault_status=0; sh scripts/run-fault.sh primary || fault_status=$$?; \
	$(COMPOSE) run --rm -T workload check || exit 1; \
	[ $$fault_status -eq 0 ]
	sh scripts/check-cluster.sh
	@cat $(RESULTS_DIR)/load.json $(RESULTS_DIR)/failover.json

chaos-sql:
	$(MAKE) failover FAULT_KIND=sql

chaos-management:
	$(MAKE) failover FAULT_KIND=management

status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T mgm0 ndb_mgm --ndb-connectstring=mgm0:1186,mgm1:1186 -e show
	sh scripts/sql.sh sql0 -e 'SELECT node_id,status FROM ndbinfo.nodes'

cli:
	$(COMPOSE) exec -e MYSQL_PWD=local-root sql0 mysql -uroot -Dorders

down:
	sh scripts/sql.sh sql0 -e 'DROP TABLE IF EXISTS subscribers, orders, customers'

docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local

.PHONY: subscriber-profiles
subscriber-profiles: images clean-results
	$(COMPOSE) run --rm -T workload subscribers
