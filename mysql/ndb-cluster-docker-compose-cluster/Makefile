export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE := docker compose --project-name mysql-ndb-cluster
COUNT ?= 1000000
CUSTOMERS ?= 10000
SUBSCRIBERS ?= 1000000
WORKERS ?= 8
BATCH ?= 100
CHAOS_RATE ?= 25000
FAULT_PARTITION ?= 0
SUBSCRIBER_LOOKUPS ?= 10000
SUBSCRIBER_SCANS ?= 20
SUBSCRIBER_OPERATIONS ?= 10000
export COUNT CUSTOMERS SUBSCRIBERS WORKERS BATCH CHAOS_RATE FAULT_PARTITION SUBSCRIBER_LOOKUPS SUBSCRIBER_SCANS SUBSCRIBER_OPERATIONS
.NOTPARALLEL:
.PHONY: docker-up images up test verify failover chaos-sql chaos-management router-failure subscriber-profiles benchmark test-production unit-test status cli down docker-down

docker-up:
	$(COMPOSE) up -d --wait --wait-timeout 360
images:
	$(COMPOSE) build workload
up: images
	python3 scripts/control.py up
test:
	python3 scripts/control.py test
	$(MAKE) subscriber-profiles
verify:
	python3 scripts/control.py verify
failover:
	python3 scripts/control.py data
chaos-sql:
	python3 scripts/control.py sql
chaos-management:
	python3 scripts/control.py management
router-failure:
	python3 scripts/control.py router
subscriber-profiles:
	python3 scripts/control.py subscribers
benchmark:
	python3 scripts/control.py benchmark
test-production:
	$(MAKE) test
	@for partition in 0 1 2; do $(MAKE) failover FAULT_PARTITION=$$partition || exit 1; done
	@for node in sql0 sql1; do $(MAKE) chaos-sql FAULT_NODE=$$node || exit 1; done
	@for node in mgm0 mgm1; do $(MAKE) chaos-management FAULT_NODE=$$node || exit 1; done
	$(MAKE) router-failure
	$(MAKE) subscriber-profiles
	$(MAKE) verify
unit-test:
	$(COMPOSE) build workload
	python3 -m unittest discover -s tests -p 'test_*.py'
status:
	$(COMPOSE) ps
	python3 scripts/control.py status
cli:
	$(COMPOSE) exec -e MYSQL_PWD=local-root sql0 mysql -uroot -Dorders
down:
	python3 scripts/control.py down
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local
	@if docker image inspect mysql-ndb-workload:dev >/dev/null 2>&1; then docker image rm mysql-ndb-workload:dev; fi
