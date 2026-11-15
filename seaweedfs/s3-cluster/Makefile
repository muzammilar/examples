SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= seaweedfs-s3-cluster
RESULTS_DIR ?= results/verified
OBJECTS ?= 10000
LARGE_OBJECTS ?= 128
BENCH_SMALL_REQUESTS ?= 10000
BENCH_REQUESTS ?= 256
BENCH_LARGE_REQUESTS ?= 128
FAULT_DURATION ?= 600
export BENCH_SMALL_REQUESTS BENCH_REQUESTS BENCH_LARGE_REQUESTS FAULT_DURATION
export COMPOSE_PROJECT_NAME RESULTS_DIR OBJECTS LARGE_OBJECTS
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
.NOTPARALLEL:
.PHONY: docker-up build up test benchmark failover disk-failure repair status cli down docker-down
docker-up:
	mkdir -p $(RESULTS_DIR)
	$(COMPOSE) up -d --wait --wait-timeout 300
build:
	$(COMPOSE) build tools
up: build
	printf '%s\n' 'fs.configure -locationPrefix=/buckets -replication=200 -fsync=true -apply' | $(COMPOSE) exec -T master0 weed shell -master=master0:9333,master1:9333,master2:9333 -filer=filer0:8888
	$(RUN) create
	$(RUN) seed
test:
	$(RUN) unit
	$(RUN) test
benchmark:
	python3 ../../object-storage/benchmark.py
failover:
	STORE=seaweedfs python3 ../../object-storage/fault.py process
disk-failure:
	STORE=seaweedfs python3 ../../object-storage/fault.py disk
repair:
	printf '%s\n' 'lock' 'volume.fix.replication -apply' 'unlock' | $(COMPOSE) exec -T master0 weed shell -master=master0:9333,master1:9333,master2:9333 -filer=filer0:8888
status:
	$(COMPOSE) ps
cli:
	$(COMPOSE) exec master0 weed shell -master=master0:9333,master1:9333,master2:9333 -filer=filer0:8888
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@if docker image inspect '$(COMPOSE_PROJECT_NAME)-tools:dev' >/dev/null 2>&1; then docker image rm '$(COMPOSE_PROJECT_NAME)-tools:dev'; fi

.PHONY: metadata-failure control-failure filer-failure gateway-failure role-failures
metadata-failure:
	FAULT_TARGET=etcd0 STORE=seaweedfs python3 ../../object-storage/fault.py process
control-failure:
	FAULT_TARGET=master0 STORE=seaweedfs python3 ../../object-storage/fault.py process
filer-failure:
	FAULT_TARGET=filer0 STORE=seaweedfs python3 ../../object-storage/fault.py process
gateway-failure:
	FAULT_TARGET=s3api0 STORE=seaweedfs python3 ../../object-storage/fault.py process
role-failures: metadata-failure control-failure filer-failure gateway-failure

.PHONY: replica-test
replica-test:
	$(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) --entrypoint python tools /app/seaweed_bytes.py
	python3 scripts/metadata_audit.py
