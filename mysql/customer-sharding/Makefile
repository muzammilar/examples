COMPOSE := docker compose
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
BULK ?= 1
RATE ?= 0
CHAOS_RATE ?= 25000
FAULT_SHARD ?= -55
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)
RUN_ID := $(RUN_ID)
RESULTS_DIR := results/$(RUN_ID)
export COUNT CUSTOMERS WORKERS BATCH BULK RATE FAULT_SHARD RUN_ID RESULTS_DIR
.NOTPARALLEL:
.PHONY: docker-up up test failover status cli down docker-down reset clean-results

docker-up:
	$(COMPOSE) up -d --wait --wait-timeout 300

up:
	sh scripts/provision.sh
	$(COMPOSE) --profile recovery up -d --wait vtorc
	$(COMPOSE) build workload
	$(COMPOSE) run --rm -T workload seed

clean-results:
	@mkdir -p $(RESULTS_DIR); chmod 777 $(RESULTS_DIR)
	@test ! -f $(RESULTS_DIR)/acknowledged.jsonl || { echo 'Use a fresh RUN_ID' >&2; exit 1; }
	sh scripts/phase.sh baseline

reset:
	@set -eu; for shard in -55 55-aa aa-; do \
	  primary=$$(sh scripts/primary.sh $$shard); \
	  sh scripts/sql.sh $$primary -Dvt_orders -e 'TRUNCATE TABLE orders'; \
	done

test: images clean-results reset
	$(COMPOSE) run --rm -T workload load
	$(COMPOSE) run --rm -T workload check
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

status:
	$(COMPOSE) --profile recovery ps
	sh scripts/ctl.sh GetTablets

cli:
	$(COMPOSE) exec vtgate mysql -h127.0.0.1 -P15306 -uroot orders

down:
	$(COMPOSE) exec -T vtgate mysql -h127.0.0.1 -P15306 -uroot orders -e 'DROP TABLE IF EXISTS orders, customers'

docker-down:
	$(COMPOSE) --profile recovery --profile tools down --volumes --remove-orphans --rmi local

.PHONY: images
images:
	$(COMPOSE) build workload
