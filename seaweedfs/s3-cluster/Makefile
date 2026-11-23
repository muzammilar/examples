export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests:$(CURDIR)/s3-workload
SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= seaweedfs-s3-cluster
RESULTS_DIR ?= results/recovery
OBJECTS ?= 10000
LARGE_OBJECTS ?= 128
BENCH_SMALL_REQUESTS ?= 10000
BENCH_REQUESTS ?= 256
BENCH_LARGE_REQUESTS ?= 128
FAULT_DURATION ?= 600
export BENCH_SMALL_REQUESTS BENCH_REQUESTS BENCH_LARGE_REQUESTS FAULT_DURATION
export COMPOSE_PROJECT_NAME RESULTS_DIR OBJECTS LARGE_OBJECTS
COMPOSE := docker compose --project-name $(COMPOSE_PROJECT_NAME)
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
.NOTPARALLEL:
.PHONY: docker-up build up test benchmark failover disk-failure repair status cli down docker-down
docker-up:
	mkdir -p $(RESULTS_DIR)
	$(COMPOSE) up -d --wait --wait-timeout 300
build:
	python3 scripts/prepare_build.py
	$(COMPOSE) build tools
up: build
	printf '%s\n' 'fs.configure -locationPrefix=/buckets -replication=200 -fsync=true -apply' | $(COMPOSE) exec -T master0 weed shell -master=master0:9333,master1:9333,master2:9333 -filer=filer0:8888
	python3 scripts/control.py up
test:
	$(RUN) unit
	$(RUN) test
benchmark:
	python3 scripts/benchmark.py
failover:
	python3 scripts/control.py process
disk-failure:
	python3 scripts/control.py disk
repair:
	printf '%s\n' 'lock' 'volume.fix.replication -apply' 'unlock' | $(COMPOSE) exec -T master0 weed shell -master=master0:9333,master1:9333,master2:9333 -filer=filer0:8888
status:
	$(COMPOSE) ps
cli:
	$(COMPOSE) exec master0 weed shell -master=master0:9333,master1:9333,master2:9333 -filer=filer0:8888
down:
	$(RUN) drop
docker-down:
	python3 scripts/control.py docker-down

.PHONY: metadata-failure control-failure filer-failure gateway-failure role-failures
metadata-failure:
	FAULT_ROLE=etcd python3 scripts/control.py process
control-failure:
	FAULT_ROLE=master python3 scripts/control.py process
filer-failure:
	FAULT_TARGET=filer0 python3 scripts/control.py process
gateway-failure:
	FAULT_TARGET=s3api0 python3 scripts/control.py process
role-failures: metadata-failure control-failure filer-failure gateway-failure

.PHONY: replica-test
replica-test:
	$(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) --entrypoint python tools /app/seaweed_bytes.py
	python3 scripts/metadata_audit.py

.PHONY: verify test-production unit-test
verify:
	python3 scripts/control.py verify
unit-test:
	$(RUN) unit
	$(COMPOSE) run --rm --entrypoint python tools -m unittest -v test_traffic
	python3 -m unittest -v test_control test_metadata test_native test_teardown
test-production:
	$(MAKE) test
	$(MAKE) failover
	$(MAKE) disk-failure
	$(MAKE) role-failures
	$(MAKE) verify

.PHONY: cold-reopen
cold-reopen:
	python3 scripts/cold_reopen.py

.PHONY: report
report:
	python3 scripts/report.py
