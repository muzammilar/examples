SHELL := /bin/sh
COMPOSE := docker compose --project-name postgres-customer-sharding
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
CHAOS_RATE ?= 25000
FAULT_SHARD ?= 0
export COUNT CUSTOMERS WORKERS BATCH CHAOS_RATE FAULT_SHARD
.NOTPARALLEL:
.PHONY: docker-up up images test verify failover chaos-replica-lag benchmark test-production status cli down docker-down unit-test

docker-up:
	$(COMPOSE) up --build -d --wait --wait-timeout 300
images:
	$(COMPOSE) build workload
up: images
	python3 control.py up
test:
	python3 control.py test
verify:
	python3 control.py verify
failover:
	python3 control.py failover
chaos-replica-lag:
	python3 control.py replica-lag
benchmark:
	python3 control.py benchmark
test-production:
	$(MAKE) test
	@for shard in 0 1 2; do $(MAKE) failover FAULT_SHARD=$$shard || exit 1; done
	@for shard in 0 1 2; do $(MAKE) chaos-replica-lag FAULT_SHARD=$$shard || exit 1; done
	$(MAKE) verify
unit-test:
	$(COMPOSE) build workload
	python3 -m unittest discover -s . -p 'test_*.py'
status:
	$(COMPOSE) ps
	python3 control.py status
cli:
	$(COMPOSE) run --rm client psql -h pgdog -p 6432 -U postgres -d orders
down:
	python3 control.py down
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local
	@if docker image inspect postgres-customer-workload:dev >/dev/null 2>&1; then docker image rm postgres-customer-workload:dev; fi
