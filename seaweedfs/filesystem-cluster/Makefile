SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
COMPOSE := docker compose --project-name seaweedfs-filesystem-cluster
export COMPOSE_PROJECT_NAME := seaweedfs-filesystem-cluster
FILES ?= 128
PAYLOAD_BYTES ?= 262144
export FILES PAYLOAD_BYTES
.NOTPARALLEL:
.PHONY: build docker-up up test verify status down docker-down
build:
	$(COMPOSE) build files-writer
docker-up: build
	$(PYTHON) scripts/control.py docker-up
up: build
	$(PYTHON) scripts/control.py up
test:
	$(PYTHON) scripts/control.py test
verify:
	$(PYTHON) scripts/control.py verify
status:
	$(COMPOSE) ps
down:
	$(PYTHON) scripts/control.py down
docker-down:
	$(PYTHON) scripts/control.py docker-down

.PHONY: locks filer-failure test-production
locks:
	$(PYTHON) scripts/production.py locks
filer-failure:
	$(PYTHON) scripts/production.py filer-failure
test-production:
	$(MAKE) verify
	$(MAKE) locks
	$(MAKE) filer-failure
	$(MAKE) verify

.PHONY: resources
resources:
	$(PYTHON) scripts/storage_resources.py

.PHONY: cli
cli:
	$(COMPOSE) exec master0 weed shell -master=master0:9333

.PHONY: unit-test
unit-test:
	$(PYTHON) -m unittest discover -s tests -v
