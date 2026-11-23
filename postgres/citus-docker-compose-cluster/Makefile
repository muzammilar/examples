export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE := docker compose --project-name postgres-citus-orders
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
CHAOS_RATE ?= 25000
FAULT_SHARD ?= 1
ANALYTICS_QUERIES ?= 25
ANALYTICS_REQUESTS ?= 200
export COUNT CUSTOMERS WORKERS BATCH CHAOS_RATE FAULT_SHARD ANALYTICS_QUERIES ANALYTICS_REQUESTS
.NOTPARALLEL:
.PHONY: docker-up images up test verify benchmark failover failover-coordinator chaos-replica-lag scale-out scale-in tenant-analytics test-production unit-test status cli down docker-down

docker-up:
	$(COMPOSE) up --build -d --wait --wait-timeout 300
images:
	$(COMPOSE) build workload
up: images
	python3 scripts/control.py up
test:
	python3 scripts/control.py test
	$(MAKE) tenant-analytics
verify:
	python3 scripts/control.py verify
benchmark:
	python3 scripts/control.py benchmark
failover:
	python3 scripts/control.py failover
failover-coordinator:
	$(MAKE) failover FAULT_SHARD=0
chaos-replica-lag:
	python3 scripts/control.py replica-lag
scale-out:
	python3 scripts/control.py scale-out
scale-in:
	python3 scripts/control.py scale-in
tenant-analytics:
	python3 scripts/control.py analytics
test-production:
	$(MAKE) test
	@for group in 0 1 2 3; do $(MAKE) failover FAULT_SHARD=$$group || exit 1; done
	@for group in 0 1 2 3; do $(MAKE) chaos-replica-lag FAULT_SHARD=$$group || exit 1; done
	$(MAKE) scale-out
	$(MAKE) tenant-analytics
	$(MAKE) scale-in
	$(MAKE) verify
unit-test:
	$(COMPOSE) build workload
	python3 -m unittest discover -s tests -p 'test_*.py'
status:
	$(COMPOSE) --profile scale ps
	python3 scripts/control.py status
cli:
	$(COMPOSE) run --rm client psql -h router -U postgres -d postgres
down:
	python3 scripts/control.py down
docker-down:
	$(COMPOSE) --profile scale --profile tools down --volumes --remove-orphans --rmi local
	@for image in postgres-citus-workload:dev postgres-citus-patroni:dev; do if docker image inspect $$image >/dev/null 2>&1; then docker image rm $$image || exit 1; fi; done

.PHONY: test-placement-guard
test-placement-guard:
	python3 scripts/placement_guard.py
