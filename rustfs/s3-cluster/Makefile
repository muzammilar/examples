SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= rustfs-s3-cluster
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
docker-up: build
	mkdir -p $(RESULTS_DIR)
	$(COMPOSE) up -d --wait --wait-timeout 600
build:
	$(COMPOSE) build tools
up: build
	$(RUN) create
	$(RUN) seed
test:
	$(RUN) unit
	$(RUN) test
benchmark:
	python3 ../../object-storage/benchmark.py
failover:
	STORE=rustfs python3 ../../object-storage/fault.py process
disk-failure:
	STORE=rustfs python3 ../../object-storage/fault.py disk
repair:
	$(COMPOSE) run --rm --entrypoint python tools /app/rustfs_admin.py
status:
	$(COMPOSE) ps
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@if docker image inspect '$(COMPOSE_PROJECT_NAME)-tools:dev' >/dev/null 2>&1; then docker image rm '$(COMPOSE_PROJECT_NAME)-tools:dev'; fi
