SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= ceph-s3-cluster
RESULTS_DIR ?= results/verified
OBJECTS ?= 1000
LARGE_OBJECTS ?= 64
BENCH_SMALL_REQUESTS ?= 1000
BENCH_REQUESTS ?= 64
BENCH_LARGE_REQUESTS ?= 32
FAULT_DURATION ?= 1800
export BENCH_SMALL_REQUESTS BENCH_REQUESTS BENCH_LARGE_REQUESTS FAULT_DURATION
export COMPOSE_PROJECT_NAME RESULTS_DIR OBJECTS LARGE_OBJECTS
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
CTL := $(COMPOSE) run --rm control
.NOTPARALLEL:
.PHONY: build docker-up up test benchmark failover disk-failure status cli down docker-down
build:
	@test "$$(docker info --format '{{.Architecture}}')" = aarch64 || { echo 'This pinned Ceph guest/package lab requires an ARM64 Docker engine.'; exit 1; }
	$(COMPOSE) build guests tools
docker-up: build
	mkdir -p $(RESULTS_DIR)
	$(COMPOSE) up -d guests
	$(CTL) /opt/lab/app/control.py ready
up:
	$(CTL) /opt/lab/app/bootstrap.py
	$(COMPOSE) up -d --wait s3
	$(RUN) create
	$(RUN) seed
test:
	$(RUN) unit
	$(RUN) test
	@for port in 9001 9002 9003; do $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) -e S3_ENDPOINT=http://guests:$$port tools probe || exit $$?; done
	$(CTL) /opt/lab/app/recovery.py audit
	$(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) --entrypoint python tools /app/proxy_audit.py rgw rgw0 rgw1 rgw2
benchmark:
	python3 ../../object-storage/benchmark.py
failover:
	python3 scripts/fault.py process
disk-failure:
	python3 scripts/fault.py disk
status:
	$(COMPOSE) ps
	$(CTL) /opt/lab/app/control.py ssh 1 'ceph -s'
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh control
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@for image in '$(COMPOSE_PROJECT_NAME)-guests:dev' '$(COMPOSE_PROJECT_NAME)-tools:dev'; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
