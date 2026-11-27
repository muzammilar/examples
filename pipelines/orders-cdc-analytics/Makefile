SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name orders-cdc-analytics
export COMPOSE_PROJECT_NAME := orders-cdc-analytics
PYTHON ?= python3
ORDER_COUNT ?= 1000000
CUSTOMER_COUNT ?= 10000
export ORDER_COUNT CUSTOMER_COUNT
.NOTPARALLEL:
.PHONY: check docker-up up test verify worker-failure delete-test verify-replicas test-production resources status cli down docker-down
check:
	$(COMPOSE) build orders
docker-up:
	$(PYTHON) scripts/lifecycle.py docker-up
up:
	$(PYTHON) scripts/lifecycle.py up
test:
	$(PYTHON) scripts/lifecycle.py test
verify:
	$(PYTHON) scripts/lifecycle.py verify
worker-failure:
	$(PYTHON) scripts/worker_failure.py
delete-test:
	$(PYTHON) scripts/lifecycle.py delete-test
verify-replicas:
	$(PYTHON) scripts/lifecycle.py verify-replicas
test-production:
	$(MAKE) verify
	$(MAKE) worker-failure
	$(MAKE) delete-test
	$(MAKE) verify-replicas
	$(MAKE) verify-routing
resources:
	$(PYTHON) scripts/resources.py
status:
	$(COMPOSE) ps
cli:
	$(COMPOSE) exec fe1 mysql -h127.0.0.1 -P9030 -uroot
down:
	$(PYTHON) scripts/lifecycle.py down
docker-down:
	$(PYTHON) scripts/lifecycle.py docker-down

.PHONY: benchmark
benchmark:
	$(PYTHON) scripts/benchmark.py

.PHONY: resume-up
resume-up:
	$(PYTHON) scripts/lifecycle.py resume-up

.PHONY: replay dimension-test
replay:
	$(PYTHON) scripts/replay.py
dimension-test:
	$(PYTHON) scripts/lifecycle.py dimension-test

.PHONY: verify-routing
verify-routing:
	$(PYTHON) scripts/routing.py

.PHONY: repair-connect-topics
repair-connect-topics:
	$(PYTHON) scripts/connect_replication.py

.PHONY: report
report:
	$(PYTHON) scripts/curate.py
