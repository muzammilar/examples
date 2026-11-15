SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= garage-s3-cluster
RESULTS_DIR ?= results/verified
OBJECTS ?= 10000
LARGE_OBJECTS ?= 128
S3_ACCESS_KEY ?= GK0123456789abcdef01234567
S3_SECRET_KEY ?= local-only-object-storage-secret-key
BENCH_SMALL_REQUESTS ?= 10000
BENCH_REQUESTS ?= 256
BENCH_LARGE_REQUESTS ?= 128
FAULT_DURATION ?= 600
export BENCH_SMALL_REQUESTS BENCH_REQUESTS BENCH_LARGE_REQUESTS FAULT_DURATION
export COMPOSE_PROJECT_NAME RESULTS_DIR OBJECTS LARGE_OBJECTS S3_ACCESS_KEY S3_SECRET_KEY
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
SERVICES := garage0 garage1 garage2 garage3 garage4 garage5 garage6 garage7 garage8 s3
.NOTPARALLEL:
.PHONY: config docker-up build up test benchmark failover disk-failure repair status cli down docker-down
config:
	python3 scripts/config.py
docker-up: config
	$(COMPOSE) up -d --wait $(SERVICES)
build:
	$(COMPOSE) build tools
up: config build
	python3 scripts/runtime_config.py
	python3 scripts/bootstrap.py
	$(RUN) create
	$(RUN) seed
test:
	$(RUN) unit
	$(RUN) test
benchmark:
	python3 ../../object-storage/benchmark.py
failover:
	python3 scripts/runtime_config.py
	STORE=garage python3 ../../object-storage/fault.py process
disk-failure:
	python3 scripts/runtime_config.py
	STORE=garage python3 ../../object-storage/fault.py disk
repair:
	$(COMPOSE) exec -T garage0 /garage repair --yes --all-nodes blocks
status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T garage0 /garage status
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@if docker image inspect '$(COMPOSE_PROJECT_NAME)-tools:dev' >/dev/null 2>&1; then docker image rm '$(COMPOSE_PROJECT_NAME)-tools:dev'; fi
	rm -rf config/generated
