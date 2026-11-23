export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests:$(CURDIR)/s3-workload
SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= ceph-s3-cluster
RESULTS_DIR ?= results/recovery
OBJECTS ?= 1000
LARGE_OBJECTS ?= 64
BENCH_SMALL_REQUESTS ?= 1000
BENCH_REQUESTS ?= 64
BENCH_LARGE_REQUESTS ?= 32
FAULT_DURATION ?= 1800
export BENCH_SMALL_REQUESTS BENCH_REQUESTS BENCH_LARGE_REQUESTS FAULT_DURATION
export COMPOSE_PROJECT_NAME RESULTS_DIR OBJECTS LARGE_OBJECTS
COMPOSE := docker compose --project-name $(COMPOSE_PROJECT_NAME)
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
CTL := $(COMPOSE) run --rm control
.NOTPARALLEL:
.PHONY: build docker-up up test benchmark failover disk-failure status cli down docker-down
build:
	@test "$$(docker info --format '{{.Architecture}}')" = aarch64 || { echo 'This pinned Ceph guest/package lab requires an ARM64 Docker engine.'; exit 1; }
	python3 scripts/prepare_build.py
	$(COMPOSE) build guests tools
docker-up: build
	mkdir -p $(RESULTS_DIR)
	python3 scripts/capacity.py
	$(COMPOSE) up -d guests
	$(CTL) /opt/lab/native/guest.py ready
up:
	python3 scripts/control.py up
test:
	$(RUN) unit
	$(RUN) test
	@for port in 9001 9002 9003; do $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) -e S3_ENDPOINT=http://guests:$$port tools probe || exit $$?; done
	$(CTL) /opt/lab/native/recovery.py audit
	$(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) --entrypoint python tools /app/proxy_audit.py rgw rgw0 rgw1 rgw2
benchmark:
	python3 scripts/benchmark.py
failover:
	python3 scripts/control.py guest
disk-failure:
	python3 scripts/control.py disk
status:
	$(COMPOSE) ps
	$(CTL) /opt/lab/native/guest.py ssh 1 'ceph -s'
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh control
.PHONY: recover-guest-init
recover-guest-init:
	$(CTL) /opt/lab/native/guest.py repair-init
down:
	$(RUN) drop
docker-down:
	python3 scripts/control.py docker-down

.PHONY: verify repair recover-bootstrap rebuilt-member-failure gateway-failure test-production unit-test
verify:
	python3 scripts/control.py verify
.PHONY: cold-reopen report
cold-reopen:
	python3 scripts/cold_reopen.py
report:
	python3 scripts/report.py
repair:
	python3 scripts/control.py repair
recover-bootstrap:
	python3 scripts/control.py recover-bootstrap
RECOVERY_CASE ?=
export RECOVERY_CASE
.PHONY: recover-replacement
recover-replacement:
	python3 scripts/control.py recover-replacement
rebuilt-member-failure:
	python3 scripts/control.py rebuilt
gateway-failure:
	python3 scripts/control.py router
test-production:
	$(MAKE) test
	$(MAKE) failover
	$(MAKE) disk-failure
	$(MAKE) rebuilt-member-failure
	$(MAKE) gateway-failure
	$(MAKE) verify
unit-test:
	$(RUN) unit
	$(COMPOSE) run --rm --entrypoint python tools -m unittest -v test_traffic test_reader
	python3 -m unittest -v test_capacity test_native_state test_teardown test_report
