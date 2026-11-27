SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
COMPOSE := docker compose --project-name seaweedfs-filesystem-cluster
export COMPOSE_PROJECT_NAME := seaweedfs-filesystem-cluster
FILES ?= 128
PAYLOAD_BYTES ?= 262144
COMPARE_FILES ?= 256
NATIVE_HTTP ?= 0
export COMPARE_FILES
export FILES PAYLOAD_BYTES
.NOTPARALLEL:
.PHONY: build docker-up up test verify status down docker-down
build:
	$(COMPOSE) build files-writer
docker-up: build
	$(PYTHON) scripts/control.py docker-up
up: build
	$(PYTHON) scripts/control.py up
	@if [ "$(NATIVE_HTTP)" = 1 ]; then $(MAKE) api-up; fi
test:
	$(PYTHON) scripts/control.py test
	@if [ "$(NATIVE_HTTP)" = 1 ]; then $(MAKE) api-test; fi
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

.PHONY: api-up api-test api-verify
api-up:
	$(PYTHON) projects/native-http/http_project.py up
api-test:
	$(PYTHON) projects/native-http/http_project.py test
api-verify:
	$(PYTHON) projects/native-http/http_project.py verify

.PHONY: api-compare
api-compare:
	$(PYTHON) projects/native-http/compare.py

.PHONY: api-filer-failure api-test-production
api-filer-failure:
	$(PYTHON) projects/native-http/filer_failure.py
api-test-production:
	$(MAKE) api-verify
	$(MAKE) api-filer-failure
	$(MAKE) api-verify
