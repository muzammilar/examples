ANALYTICS_QUERIES ?= 25
ANALYTICS_REQUESTS ?= 200
export ANALYTICS_QUERIES ANALYTICS_REQUESTS
COMPOSE := docker compose
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
BULK ?= 1
RATE ?= 0
CHAOS_RATE ?= 25000
FAULT_SHARD ?= 1
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)
RUN_ID := $(RUN_ID)
RESULTS_DIR := results/$(RUN_ID)
export COUNT CUSTOMERS WORKERS BATCH BULK RATE FAULT_SHARD RUN_ID RESULTS_DIR
.NOTPARALLEL:
.PHONY: extra-workers scale-out scale-in docker-up up test failover failover-coordinator chaos-replica-lag status cli down docker-down reset clean-results

docker-up:
	$(COMPOSE) up --build -d --wait --wait-timeout 300

up:
	sh scripts/provision.sh
	$(COMPOSE) build workload
	$(COMPOSE) run --rm -T workload seed

reset:
	@node=$$(sh scripts/primary.sh 0); $(COMPOSE) exec -T $$node psql -U postgres -v ON_ERROR_STOP=1 -c 'TRUNCATE orders'

clean-results:
	@mkdir -p $(RESULTS_DIR); chmod 777 $(RESULTS_DIR)
	@test ! -f $(RESULTS_DIR)/acknowledged.jsonl || { echo "Choose a fresh RUN_ID" >&2; exit 1; }
	@sh scripts/phase.sh baseline

test: images clean-results reset
	$(COMPOSE) run --rm -T workload load
	$(COMPOSE) run --rm -T workload check
	$(COMPOSE) run --rm -T workload analytics
	$(COMPOSE) run --rm -T workload check
	sh scripts/verify-cluster.sh
	$(MAKE) status

failover: RATE = $(CHAOS_RATE)
failover: images clean-results reset
	@fault_status=0; sh scripts/run-fault.sh primary || fault_status=$$?; \
	$(COMPOSE) run --rm -T workload check || exit 1; \
	[ $$fault_status -eq 0 ]
	sh scripts/verify-cluster.sh
	@cat $(RESULTS_DIR)/load.json $(RESULTS_DIR)/failover.json
	$(MAKE) status

failover-coordinator:
	$(MAKE) failover FAULT_SHARD=0

chaos-replica-lag: RATE = $(CHAOS_RATE)
chaos-replica-lag: images clean-results reset
	@fault_status=0; sh scripts/run-fault.sh replica-lag || fault_status=$$?; \
	$(COMPOSE) run --rm -T workload check || exit 1; \
	[ $$fault_status -eq 0 ]
	sh scripts/verify-cluster.sh
	@cat $(RESULTS_DIR)/load.json $(RESULTS_DIR)/failover.json

status:
	$(COMPOSE) ps
	@node=$$(sh scripts/primary.sh 0); \
	$(COMPOSE) exec -T $$node psql -U postgres -c 'SELECT nodeid,groupid,nodename,noderole,isactive FROM pg_dist_node ORDER BY groupid,nodeid' -c 'SELECT nodename,count(*) AS shards FROM citus_shards GROUP BY nodename' -c 'SELECT count(*),sum(amount) FROM orders'

cli:
	$(COMPOSE) run --rm client psql -h router -U postgres -d postgres

down:
	@node=$$(sh scripts/primary.sh 0); $(COMPOSE) exec -T $$node psql -U postgres -v ON_ERROR_STOP=1 -c 'DROP TABLE IF EXISTS orders, customers'

docker-down:
	$(COMPOSE) --profile scale --profile tools down --volumes --remove-orphans --rmi local

extra-workers:
	$(COMPOSE) --profile scale up -d --wait --wait-timeout 180 s4n0 s4n1 s4n2 s5n0 s5n1 s5n2

scale-out: RATE = $(CHAOS_RATE)
scale-out: extra-workers images clean-results reset
	@fault_status=0; sh scripts/run-fault.sh scale-out || fault_status=$$?; \
	$(COMPOSE) run --rm -T workload check || exit 1; \
	[ $$fault_status -eq 0 ]
	sh scripts/verify-cluster.sh
	$(MAKE) status

scale-in: RATE = $(CHAOS_RATE)
scale-in: images clean-results reset
	@fault_status=0; sh scripts/run-fault.sh scale-in || fault_status=$$?; \
	$(COMPOSE) run --rm -T workload check || exit 1; \
	[ $$fault_status -eq 0 ]
	sh scripts/verify-cluster.sh
	$(MAKE) status

.PHONY: images
images:
	$(COMPOSE) build workload

.PHONY: tenant-analytics
tenant-analytics: images
	@mkdir -p $(RESULTS_DIR); chmod 777 $(RESULTS_DIR)
	$(COMPOSE) run --rm -T workload analytics
