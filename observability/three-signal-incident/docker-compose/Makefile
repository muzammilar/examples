SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
export LAB_UID := $(shell id -u)
export COMPOSE_PROJECT_NAME := obs-three-signal-incident
COMPOSE := docker compose --project-name $(COMPOSE_PROJECT_NAME)
.NOTPARALLEL:
.PHONY: check
check:
	$(PYTHON) -m unittest discover -s tests
	$(PYTHON) scripts/prepare.py
	$(COMPOSE) --profile greptime --profile victoria --profile grafana --profile tools config --quiet
	$(COMPOSE) build incident-workload

.PHONY: docker-up up driver-control status
docker-up: check
	$(PYTHON) scripts/observe.py infrastructure PYTHON=$(PYTHON)
up:
	$(PYTHON) scripts/control.py up
driver-control:
	$(PYTHON) scripts/observe.py driver-case PYTHON=$(PYTHON)
status:
	$(PYTHON) scripts/control.py status

.PHONY: infrastructure driver-case
infrastructure:
	$(PYTHON) scripts/control.py docker-up
driver-case:
	$(PYTHON) scripts/control.py driver-control

.PHONY: resume-control resume-case
export CASE BACKEND
resume-control:
	$(PYTHON) scripts/observe.py resume-case PYTHON=$(PYTHON)
resume-case:
	$(PYTHON) scripts/control.py resume-control

.PHONY: diagnose-projection projection-case
diagnose-projection:
	$(PYTHON) scripts/observe.py projection-case PYTHON=$(PYTHON)
projection-case:
	$(PYTHON) scripts/control.py diagnose-projection

.PHONY: test test-case correctness correctness-case collector-crash collector-case verify-retained retained-case
test:
	$(PYTHON) scripts/observe.py test-case PYTHON=$(PYTHON)
test-case:
	$(PYTHON) scripts/control.py test
correctness:
	$(PYTHON) scripts/observe.py correctness-case PYTHON=$(PYTHON)
correctness-case:
	$(PYTHON) scripts/control.py correctness
collector-crash:
	$(PYTHON) scripts/observe.py collector-case PYTHON=$(PYTHON)
collector-case:
	$(PYTHON) scripts/control.py collector-crash
verify-retained:
	$(PYTHON) scripts/observe.py retained-case PYTHON=$(PYTHON)
retained-case:
	$(PYTHON) scripts/control.py verify-retained

.PHONY: down remove docker-down
down:
	$(PYTHON) scripts/observe.py remove PYTHON=$(PYTHON)
remove:
	$(PYTHON) scripts/control.py down
docker-down:
	$(PYTHON) scripts/control.py docker-down

.PHONY: resume-experiment resume-experiment-case
resume-experiment:
	$(PYTHON) scripts/observe.py resume-experiment-case PYTHON=$(PYTHON)
resume-experiment-case:
	$(PYTHON) scripts/control.py resume-experiment

.PHONY: test-production report
# Contract failures halt qualification; collector-crash remains independently runnable.
test-production: test collector-crash
report:
	$(PYTHON) scripts/report.py "$(CASE)"
