SHELL := /bin/sh
COMPOSE := docker compose
LOCAL_UID := $(shell id -u)
LOCAL_GID := $(shell id -g)
export LOCAL_UID LOCAL_GID
DISK_GIB ?= 16
BENCH_ROWS ?= 200000
QUERY_RUNS ?= 20
export DISK_GIB BENCH_ROWS QUERY_RUNS
ROWS ?= 2000000
CUSTOMERS ?= 10000
BATCH_ROWS ?= 10000
CLIENTS ?= 4
BATCH_DELAY_MS ?= 1000
RESULTS_DIR ?= results/manual
export ROWS CUSTOMERS BATCH_ROWS CLIENTS BATCH_DELAY_MS RESULTS_DIR
RUN := $(COMPOSE) run --rm controller
.NOTPARALLEL:
.PHONY: docker-up up test disk-test crash-test replica-test benchmark status cli down docker-down keys
keys:
	@mkdir -p state/ssh "$(RESULTS_DIR)"
	@test -f state/ssh/id_ed25519 || ssh-keygen -q -t ed25519 -N '' -f state/ssh/id_ed25519

docker-up: keys
	$(COMPOSE) build primary
	$(COMPOSE) up -d --wait primary standby

up:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) up

test: disk-test crash-test replica-test

disk-test:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) disk-test

crash-test:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) crash-start
	$(COMPOSE) up -d --wait primary
	$(RUN) crash-finish

replica-test:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) replica-test

benchmark:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) benchmark

status:
	$(COMPOSE) ps --all
	$(RUN) status

cli:
	$(COMPOSE) exec primary ssh -i /keys/id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 2222 lab@127.0.0.1

down:
	$(RUN) down

docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@if docker image inspect duckdb-zfs-lab:dev >/dev/null 2>&1; then docker image rm duckdb-zfs-lab:dev; fi
	@rm -f state/ssh/id_ed25519 state/ssh/id_ed25519.pub
	@rmdir state/ssh state 2>/dev/null || true
