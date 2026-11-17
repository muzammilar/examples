SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= rustfs-s3-cluster
RESULTS_DIR ?= results/recovery
OBJECTS ?= 10000
LARGE_OBJECTS ?= 128
BENCH_SMALL_REQUESTS ?= 10000
BENCH_REQUESTS ?= 256
BENCH_LARGE_REQUESTS ?= 128
FAULT_DURATION ?= 900
export BENCH_SMALL_REQUESTS BENCH_REQUESTS BENCH_LARGE_REQUESTS FAULT_DURATION
export COMPOSE_PROJECT_NAME RESULTS_DIR OBJECTS LARGE_OBJECTS
COMPOSE := docker compose --project-name $(COMPOSE_PROJECT_NAME)
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
.NOTPARALLEL:
.PHONY: docker-up build up test benchmark failover disk-failure repair status cli down docker-down
docker-up: build
	mkdir -p $(RESULTS_DIR)
	$(COMPOSE) up --no-deps --abort-on-container-exit --exit-code-from init init
	$(COMPOSE) up -d --wait --wait-timeout 600
build:
	python3 prepare_build.py
	$(COMPOSE) build tools
up: build
	python3 control.py up
test:
	$(RUN) unit
	$(RUN) test
benchmark:
	python3 benchmark.py
failover:
	python3 control.py process
disk-failure:
	python3 control.py disk
repair:
	python3 control.py repair
status:
	$(COMPOSE) ps
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(RUN) drop
docker-down:
	python3 control.py docker-down

.PHONY: verify unit-test gateway-failure test-production recover-bootstrap cold-reopen
verify:
	python3 control.py verify
unit-test:
	$(RUN) unit
	$(COMPOSE) run --rm --entrypoint python tools -m unittest -v test_traffic test_reader test_native test_physical
	python3 -m unittest -v test_control test_teardown
gateway-failure:
	FAULT_TARGET=s3 python3 control.py process
test-production:
	$(MAKE) test
	$(MAKE) failover FAULT_TARGET=rustfs0
	$(MAKE) failover FAULT_TARGET=rustfs3
	$(MAKE) disk-failure FAULT_TARGET=rustfs0
	$(MAKE) gateway-failure
	$(MAKE) verify
recover-bootstrap:
	python3 control.py recover-bootstrap
cold-reopen:
	python3 cold_reopen.py

.PHONY: report
report:
	python3 report.py

RECOVERY_CASE ?=
export RECOVERY_CASE
.PHONY: recover-replacement
recover-replacement:
	python3 control.py recover-replacement
