SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
PARENT := ../..
export COMPOSE_FILE := $(abspath $(PARENT)/docker-compose.yml):$(abspath compose.yaml)
export COMPOSE_PROJECT_NAME := orders-cdc-analytics
ORDER_COUNT ?= 1000000
CUSTOMER_COUNT ?= 10000
export ORDER_COUNT CUSTOMER_COUNT
.NOTPARALLEL:
.PHONY: check docker-up up test _source _compare verify down _down docker-down report
check:
	$(PYTHON) -m unittest discover -s tests
	$(PYTHON) scripts/control.py check
	docker compose --project-directory $(abspath $(PARENT)) --profile control build comparison
docker-up: check
	$(MAKE) -C $(PARENT) docker-up PYTHON=$(PYTHON)
	docker compose --project-directory $(abspath $(PARENT)) --profile control pull ch1 keeper1
up:
	$(MAKE) -C $(PARENT) up PYTHON=$(PYTHON)
	$(PYTHON) scripts/control.py up
test:
	$(PYTHON) ../../scripts/observe.py _source PYTHON=$(PYTHON)
	$(PYTHON) ../../scripts/observe.py _compare PYTHON=$(PYTHON)
	$(MAKE) serving-failure PYTHON=$(PYTHON)
_source:
	$(PYTHON) scripts/control.py source
_compare:
	$(PYTHON) scripts/control.py compare
verify:
	$(PYTHON) scripts/control.py verify
down:
	$(PYTHON) ../../scripts/observe.py _down PYTHON=$(PYTHON)
_down:
	$(PYTHON) scripts/control.py down
docker-down:
	COMPOSE_PROFILES=control $(MAKE) -C $(PARENT) docker-down PYTHON=$(PYTHON)
report:
	$(PYTHON) scripts/report.py

.PHONY: serving-failure _failure test-production
serving-failure:
	$(PYTHON) ../../scripts/observe.py _failure PYTHON=$(PYTHON)
_failure:
	$(PYTHON) scripts/control.py serving-failure
test-production: serving-failure

.PHONY: resume-compare _resume
resume-compare:
	$(PYTHON) ../../scripts/observe.py _resume PYTHON=$(PYTHON)
_resume:
	$(PYTHON) scripts/control.py resume-compare

.PHONY: resources status cli
resources:
	$(PYTHON) scripts/control.py resources
status:
	docker compose --project-directory $(abspath $(PARENT)) --profile control ps --all
cli:
	docker compose --project-directory $(abspath $(PARENT)) --profile control exec ch1 clickhouse-client --user lab --password local-control-only
