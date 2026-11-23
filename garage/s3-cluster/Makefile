export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests:$(CURDIR)/s3-workload
SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= garage-s3-cluster
RESULTS_DIR ?= results/recovery
OBJECTS ?= 10000
LARGE_OBJECTS ?= 128
S3_ACCESS_KEY ?= GK0123456789abcdef01234567
S3_SECRET_KEY ?= local-only-object-storage-secret-key
BENCH_SMALL_REQUESTS ?= 10000
BENCH_REQUESTS ?= 256
BENCH_LARGE_REQUESTS ?= 128
FAULT_DURATION ?= 900
export BENCH_SMALL_REQUESTS BENCH_REQUESTS BENCH_LARGE_REQUESTS FAULT_DURATION
export COMPOSE_PROJECT_NAME RESULTS_DIR OBJECTS LARGE_OBJECTS S3_ACCESS_KEY S3_SECRET_KEY
COMPOSE := docker compose --project-name $(COMPOSE_PROJECT_NAME)
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
SERVICES := garage0 garage1 garage2 garage3 garage4 garage5 garage6 garage7 garage8 s3
.NOTPARALLEL:
.PHONY: config docker-up build up test benchmark failover disk-failure repair status cli down docker-down
config:
	python3 scripts/config.py
docker-up: config
	$(COMPOSE) up -d --wait $(SERVICES)
build:
	python3 scripts/prepare_build.py
	$(COMPOSE) build tools
up: config build
	python3 scripts/bootstrap.py
	$(MAKE) config
	python3 scripts/runtime_config.py
	python3 scripts/control.py up
test:
	$(RUN) unit
	$(RUN) test
benchmark:
	python3 scripts/benchmark.py
failover:
	python3 scripts/runtime_config.py
	python3 scripts/control.py process
disk-failure:
	python3 scripts/runtime_config.py
	python3 scripts/control.py disk
repair:
	python3 scripts/control.py repair
status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T garage0 /garage status
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(RUN) drop
docker-down:
	python3 scripts/control.py docker-down
	rm -rf config/generated

.PHONY: verify test-production unit-test gateway-failure
verify:
	python3 scripts/control.py verify
gateway-failure:
	FAULT_TARGET=s3 python3 scripts/control.py process
unit-test:
	$(RUN) unit
	$(COMPOSE) run --rm --entrypoint python tools -m unittest -v test_traffic test_placement test_reader
	python3 -m unittest -v test_control test_teardown test_config
test-production:
	$(MAKE) test
	$(MAKE) failover
	$(MAKE) disk-failure
	$(MAKE) gateway-failure
	$(MAKE) verify

.PHONY: cold-reopen
cold-reopen:
	python3 scripts/cold_reopen.py

.PHONY: report
report:
	python3 scripts/report.py

.PHONY: recover-bootstrap
recover-bootstrap:
	python3 scripts/control.py recover-bootstrap
