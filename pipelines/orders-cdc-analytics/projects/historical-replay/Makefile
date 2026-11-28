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
.PHONY: check docker-up up test _baseline _test verify down docker-down report
check:
	$(PYTHON) -m unittest discover -s tests
	$(PYTHON) scripts/run.py check
docker-up: check
	$(MAKE) -C $(PARENT) docker-up PYTHON=$(PYTHON)
	docker compose --project-directory $(abspath $(PARENT)) --profile historical build historical historical-spark
up:
	$(MAKE) -C $(PARENT) up PYTHON=$(PYTHON)
test:
	$(PYTHON) ../../scripts/observe.py _baseline PYTHON=$(PYTHON)
	$(PYTHON) ../../scripts/observe.py _test PYTHON=$(PYTHON)
_baseline:
	$(MAKE) -C $(PARENT) test PYTHON=$(PYTHON)
_test:
	$(PYTHON) scripts/run.py test
verify:
	$(MAKE) -C $(PARENT) verify-replicas PYTHON=$(PYTHON)
down:
	$(MAKE) -C $(PARENT) down PYTHON=$(PYTHON)
	docker compose --project-directory $(abspath $(PARENT)) --profile historical stop historical
docker-down:
	COMPOSE_PROFILES=historical $(MAKE) -C $(PARENT) docker-down PYTHON=$(PYTHON)
report:
	$(PYTHON) scripts/report.py

.PHONY: replay-failure test-production
replay-failure:
	$(PYTHON) ../../scripts/observe.py _test PYTHON=$(PYTHON)
test-production: replay-failure
