HISTORY_QUERIES ?= 20
export HISTORY_QUERIES
COMPOSE := docker compose
COUNT ?= 1000000
CUSTOMERS ?= 10000
AMPLIFY ?= 256
FAULT_KIND ?= worker
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)
RUN_ID := $(RUN_ID)
export COUNT CUSTOMERS AMPLIFY FAULT_KIND RUN_ID
.NOTPARALLEL:
.PHONY: docker-up up images test failover chaos-minio chaos-catalog status cli down docker-down

docker-up:
	$(COMPOSE) build minio1
	$(COMPOSE) up -d --wait --wait-timeout 300

images:
	$(COMPOSE) build client
	@mkdir -p results; chmod 777 results

up: images
	$(COMPOSE) run --rm -T client seed

test: images
	$(COMPOSE) run --rm -T client check
	$(COMPOSE) run --rm -T client heavy
	$(COMPOSE) run --rm -T client history

failover: images
	@fault_status=0; sh scripts/failover.sh || fault_status=$$?; \
	$(COMPOSE) run --rm -T client check || exit 1; \
	[ $$fault_status -eq 0 ]

chaos-minio:
	$(MAKE) failover FAULT_KIND=minio

chaos-catalog: images
	$(COMPOSE) kill -s SIGKILL catalog
	$(COMPOSE) up -d --wait --wait-timeout 120 catalog
	$(COMPOSE) run --rm -T client check

status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T coordinator trino --execute 'SELECT node_id, state FROM system.runtime.nodes'

cli:
	$(COMPOSE) exec coordinator trino --catalog iceberg --schema example

down: images
	$(COMPOSE) run --rm -T client drop

docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local

.PHONY: order-history
order-history: images
	$(COMPOSE) run --rm -T client history
