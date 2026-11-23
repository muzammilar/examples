export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE := docker compose --project-name mysql-customer-sharding
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
CHAOS_RATE ?= 25000
FAULT_SHARD ?= -55
export COUNT CUSTOMERS WORKERS BATCH CHAOS_RATE FAULT_SHARD
.NOTPARALLEL:
.PHONY: docker-up images up test verify failover replica-pause replica-crash router-failure benchmark test-production unit-test status cli down docker-down

docker-up:
	$(COMPOSE) up -d --wait --wait-timeout 300
images:
	$(COMPOSE) build workload
up: images
	python3 scripts/control.py up
test:
	python3 scripts/control.py test
verify:
	python3 scripts/control.py verify
failover:
	python3 scripts/control.py failover
replica-pause:
	python3 scripts/control.py replica-lag
replica-crash:
	python3 scripts/control.py replica-crash
router-failure:
	python3 scripts/control.py router
benchmark:
	python3 scripts/control.py benchmark
test-production:
	$(MAKE) test
	@for shard in -55 55-aa aa-; do $(MAKE) failover FAULT_SHARD=$$shard || exit 1; done
	@for shard in -55 55-aa aa-; do $(MAKE) replica-pause FAULT_SHARD=$$shard || exit 1; done
	$(MAKE) replica-crash
	$(MAKE) router-failure
	$(MAKE) verify
unit-test:
	$(COMPOSE) build workload
	python3 -m unittest discover -s tests -p 'test_*.py'
status:
	$(COMPOSE) --profile recovery ps
	python3 scripts/control.py status
cli:
	$(COMPOSE) exec vtgate mysql -h127.0.0.1 -P15306 -uroot orders@primary
down:
	python3 scripts/control.py down
docker-down:
	$(COMPOSE) --profile recovery --profile tools down --volumes --remove-orphans --rmi local
	@if docker image inspect mysql-customer-workload:dev >/dev/null 2>&1; then docker image rm mysql-customer-workload:dev; fi
