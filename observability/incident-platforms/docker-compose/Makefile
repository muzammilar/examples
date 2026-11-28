SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3.13
export OPENOBSERVE_SERVICE ?= openobserve-sync
export LAB_UID := $(shell id -u)
export COMPOSE_PROJECT_NAME := obs-incident-platforms
.NOTPARALLEL:
.PHONY: check bootstrap bootstrap-case driver-control driver-case status
check:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) scripts/prepare.py
	$(PYTHON) scripts/control.py check
bootstrap: check
	$(PYTHON) scripts/observe.py bootstrap-case PYTHON=$(PYTHON)
bootstrap-case:
	$(PYTHON) scripts/control.py bootstrap
driver-control:
	$(PYTHON) scripts/observe.py driver-case PYTHON=$(PYTHON)
driver-case:
	$(PYTHON) scripts/control.py driver-control
status:
	$(PYTHON) scripts/control.py status

.PHONY: verify-control verify-case
export CASE
verify-control:
	$(PYTHON) scripts/observe.py verify-case PYTHON=$(PYTHON)
verify-case:
	$(PYTHON) scripts/control.py verify-control

.PHONY: full full-case ingress-crash ingress-case
full:
	$(PYTHON) scripts/observe.py full-case PYTHON=$(PYTHON)
full-case:
	$(PYTHON) scripts/control.py full
ingress-crash:
	$(PYTHON) scripts/observe.py ingress-case PYTHON=$(PYTHON)
ingress-case:
	$(PYTHON) scripts/control.py ingress-crash

.PHONY: docker-up up test down docker-down preserve-stop
docker-up: bootstrap
up:
	$(PYTHON) scripts/observe.py setup-case PYTHON=$(PYTHON)
setup-case:
	$(PYTHON) scripts/control.py up
test:
	$(PYTHON) scripts/observe.py test-case PYTHON=$(PYTHON)
test-case:
	$(PYTHON) scripts/control.py test
down:
	$(PYTHON) scripts/observe.py down-case PYTHON=$(PYTHON)
down-case:
	$(PYTHON) scripts/control.py down
docker-down:
	$(PYTHON) scripts/control.py docker-down
preserve-stop:
	$(PYTHON) scripts/control.py preserve-stop
.PHONY: setup-case test-case down-case

.PHONY: test-production
test-production:
	$(MAKE) test
	$(MAKE) full
	$(MAKE) ingress-crash

.PHONY: queries queries-case
queries:
	$(PYTHON) scripts/observe.py queries-case PYTHON=$(PYTHON)
queries-case:
	$(PYTHON) scripts/control.py queries
