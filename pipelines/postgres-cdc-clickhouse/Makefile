SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
COMPOSE := docker compose --project-name postgres-cdc-clickhouse
export COMPOSE_PROJECT_NAME := postgres-cdc-clickhouse
ORDER_COUNT ?= 1000000
CUSTOMER_COUNT ?= 10000
export ORDER_COUNT CUSTOMER_COUNT
.NOTPARALLEL:
.PHONY: check docker-up up test snapshot-recovery test-production verify status cli down docker-down report initialize remove
check:
	$(PYTHON) -m unittest discover -s tests
	$(COMPOSE) config --quiet
	$(COMPOSE) build orders-workload
docker-up: check
	$(PYTHON) scripts/control.py docker-up
up:
	$(PYTHON) scripts/observe.py initialize PYTHON=$(PYTHON)
initialize:
	$(PYTHON) scripts/control.py up
test:
	$(PYTHON) scripts/observe.py snapshot-recovery PYTHON=$(PYTHON)
snapshot-recovery:
	$(PYTHON) scripts/control.py test
test-production: test
verify:
	$(PYTHON) scripts/control.py verify
status:
	$(COMPOSE) ps --all
cli:
	$(COMPOSE) exec ch1 clickhouse-client --user lab --password local-control-only
down:
	$(PYTHON) scripts/observe.py remove PYTHON=$(PYTHON)
remove:
	$(PYTHON) scripts/control.py down
docker-down:
	$(PYTHON) scripts/control.py docker-down
report:
	$(PYTHON) scripts/curate.py
