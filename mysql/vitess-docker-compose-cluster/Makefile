SHELL := /bin/sh
COMPOSE := docker compose --project-name mysql-vitess-cluster
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
CHAOS_RATE ?= 25000
FAULT_SHARD ?= 0
export COUNT CUSTOMERS WORKERS BATCH CHAOS_RATE FAULT_SHARD
.NOTPARALLEL:
.PHONY: docker-up images up test verify failover replica-pause replica-crash router-failure benchmark test-production unit-test status cli down docker-down

docker-up:
	$(COMPOSE) up -d --wait --wait-timeout 300
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
replica-pause:
	python3 control.py replica-lag
replica-crash:
	python3 control.py replica-crash
router-failure:
	python3 control.py router
benchmark:
	python3 control.py benchmark
test-production:
	$(MAKE) test
	$(MAKE) failover
	$(MAKE) replica-pause
	$(MAKE) replica-crash
	$(MAKE) router-failure
	$(MAKE) verify
unit-test:
	$(COMPOSE) build workload
	python3 -m unittest discover -s . -p 'test_*.py'
status:
	$(COMPOSE) --profile recovery ps
	python3 control.py status
cli:
	$(COMPOSE) exec vtgate mysql -h127.0.0.1 -P15306 -uroot orders@primary
down:
	python3 control.py down
docker-down:
	$(COMPOSE) --profile recovery --profile tools down --volumes --remove-orphans --rmi local
	@if docker image inspect mysql-vitess-workload:dev >/dev/null 2>&1; then docker image rm mysql-vitess-workload:dev; fi
