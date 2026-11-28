SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
export LAB_UID := $(shell id -u)
export COMPOSE_PROJECT_NAME := obs-greptime-victoria-single
COMPOSE := docker compose --project-name $(COMPOSE_PROJECT_NAME)
.NOTPARALLEL:
.PHONY: verify-retained retained-case check docker-up infrastructure up driver-control driver-case correctness common-case test status
check:
	$(PYTHON) -m unittest discover -s tests
	$(PYTHON) scripts/prepare.py
	$(COMPOSE) --profile greptime --profile victoria --profile tools config --quiet
	$(COMPOSE) build telemetry-workload
docker-up: check
	$(PYTHON) scripts/observe.py infrastructure PYTHON=$(PYTHON)
infrastructure:
	$(PYTHON) scripts/control.py docker-up
up:
	$(PYTHON) scripts/control.py up
driver-control:
	$(PYTHON) scripts/observe.py driver-case PYTHON=$(PYTHON)
driver-case:
	$(PYTHON) scripts/control.py driver-control
correctness:
	$(PYTHON) scripts/observe.py common-case PYTHON=$(PYTHON)
common-case:
	$(PYTHON) scripts/control.py correctness
test: correctness native-correctness
status:
	$(COMPOSE) ps --all

verify-retained:
	$(PYTHON) scripts/observe.py retained-case PYTHON=$(PYTHON)
retained-case:
	$(PYTHON) scripts/control.py verify-retained

.PHONY: native-control native-case
native-control:
	$(PYTHON) scripts/observe.py native-case PYTHON=$(PYTHON)
native-case:
	$(PYTHON) scripts/control.py native-control

.PHONY: native-correctness native-full-case
native-correctness:
	$(PYTHON) scripts/observe.py native-full-case PYTHON=$(PYTHON)
native-full-case:
	$(PYTHON) scripts/control.py native-correctness

.PHONY: down remove docker-down
down:
	$(PYTHON) scripts/observe.py remove PYTHON=$(PYTHON)
remove:
	$(PYTHON) scripts/control.py down
docker-down:
	$(PYTHON) scripts/control.py docker-down

.PHONY: report
report:
	$(PYTHON) scripts/curate.py

.PHONY: live-control live-case crash-metrics metrics-fault-case crash-logs logs-fault-case
live-control:
	$(PYTHON) scripts/observe.py live-case PYTHON=$(PYTHON)
live-case:
	$(PYTHON) scripts/control.py live-control
crash-metrics:
	$(PYTHON) scripts/observe.py metrics-fault-case PYTHON=$(PYTHON)
metrics-fault-case:
	$(PYTHON) scripts/control.py crash-metrics
crash-logs:
	$(PYTHON) scripts/observe.py logs-fault-case PYTHON=$(PYTHON)
logs-fault-case:
	$(PYTHON) scripts/control.py crash-logs

.PHONY: recover-witness witness-case
export CASE
recover-witness:
	$(PYTHON) scripts/observe.py witness-case PYTHON=$(PYTHON)
witness-case:
	$(PYTHON) scripts/control.py recover-witness

.PHONY: test-production
test-production: live-control crash-metrics crash-logs verify-retained

.PHONY: recovery-report
recovery-report:
	$(PYTHON) scripts/curate_recovery.py
