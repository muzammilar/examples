export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE_PROJECT_NAME := starrocks-orders
COUNT ?= 1000000
FAULT_BATCH_ROWS ?= 1000
FAULT_PAUSE_MS ?= 1000
FAULT_SERVICE ?=
export COMPOSE_PROJECT_NAME COUNT FAULT_BATCH_ROWS FAULT_PAUSE_MS FAULT_SERVICE
COMPOSE := docker compose -f docker-compose.json
RUN := $(COMPOSE) run --rm client
.NOTPARALLEL:
.PHONY: docker-up up test benchmark down status docker-down failover disk-failure chaos-fe-leader comparison-failover comparison-disk-failure chaos-keeper-leader test-production
docker-up:
	python3 scripts/lifecycle.py $(COMPOSE_PROJECT_NAME)
	python3 scripts/prepare.py mutable-orders docker
	python3 scripts/config.py
	$(COMPOSE) build sr-fe-0 sr-be-0 client
	$(COMPOSE) up -d --no-deps sr-fe-0 sr-fe-1 sr-fe-2
	python3 scripts/ready.py
	$(COMPOSE) up -d --wait --wait-timeout 600
up:
	python3 scripts/ready.py all
	$(RUN) setup
	@if test -f results/seed.json; then $(RUN) verify; else $(RUN) seed; fi
test:
	python3 -c 'from pathlib import Path; import time; r=Path("results"); [(p.rename(p.with_name(p.stem+"-before-"+str(time.time_ns())+".json"))) for n in ("correctness.json", "recovery-history.json") if (p:=r/n).exists()]'
	$(RUN) verify
	python3 -c 'import json; from pathlib import Path; r=Path("results"); assert json.load(open(r/"correctness.json")); assert not list(r.glob("fault-*.jsonl")) or json.load(open(r/"recovery-history.json")), "missing fresh recovery reconciliation"'
benchmark:
	python3 scripts/run_benchmark.py
failover:
	python3 scripts/fault.py starrocks process
disk-failure:
	python3 scripts/fault.py starrocks disk
chaos-fe-leader:
	python3 scripts/fault.py starrocks leader
comparison-failover:
	python3 scripts/fault.py clickhouse process
comparison-disk-failure:
	python3 scripts/fault.py clickhouse disk
chaos-keeper-leader:
	python3 scripts/fault.py clickhouse leader
test-production:
	$(MAKE) test
	$(MAKE) failover
	$(MAKE) disk-failure
	$(MAKE) failover FAULT_SERVICE=sr-be-1
	$(MAKE) chaos-fe-leader
	$(MAKE) comparison-failover
	$(MAKE) comparison-disk-failure
	$(MAKE) comparison-failover FAULT_SERVICE=ch01
	$(MAKE) chaos-keeper-leader
	$(MAKE) test
down:
	$(RUN) drop
status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T sr-fe-0 mysql -h 127.0.0.1 -P 9030 -u root -e 'SHOW FRONTENDS; SHOW BACKENDS;'
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	docker image rm $(COMPOSE_PROJECT_NAME)-fe:dev $(COMPOSE_PROJECT_NAME)-be:dev $(COMPOSE_PROJECT_NAME)-client:dev
	rm -f docker-compose.json
