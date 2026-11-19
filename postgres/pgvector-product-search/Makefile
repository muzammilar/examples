SHELL := /bin/sh
MODE ?= cluster
ifeq ($(filter $(MODE),single cluster),)
$(error MODE must be single or cluster)
endif
RESULTS_DIR ?= results/production/$(MODE)
DOCUMENTS ?= 100000
QUERIES ?= 1000
SEED ?= 20261115
DURATION_SECONDS ?= 1800
UPDATE_BATCH ?= 10
NODE_DOWN_SECONDS ?= 45
export DISK_CASE
export MODE RESULTS_DIR DOCUMENTS QUERIES SEED DURATION_SECONDS UPDATE_BATCH NODE_DOWN_SECONDS
ifeq ($(MODE),single)
PG_NODES := single
SERVICES := single
else
PG_NODES := pg0,pg1,pg2
SERVICES := etcd0 etcd1 etcd2 pg0 pg1 pg2
endif
export PG_NODES
COMPOSE_FILE := $(CURDIR)/docker-compose.yml
COMPOSE_PROJECT_NAME := pgvector-product-search
export COMPOSE_FILE COMPOSE_PROJECT_NAME
COMPOSE := docker compose --file "$(COMPOSE_FILE)" --project-name $(COMPOSE_PROJECT_NAME) --profile $(MODE)
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
CONTROL := python3 control.py
.NOTPARALLEL:
.PHONY: build run corpus docker-up up test check-controls benchmark failover replica-crash disk-failure resume-disk-recovery rebuilt-crash cold-reopen crash-reopen test-production status cli down docker-down
corpus:
	python3 corpus.py
build:
	python3 prepare_build.py
	$(COMPOSE) build tools inventory-workload
docker-up: corpus build
	$(CONTROL) preflight
	@mkdir -p "$(RESULTS_DIR)"
ifeq ($(MODE),cluster)
	$(COMPOSE) build pg0
endif
	$(COMPOSE) up -d --wait $(SERVICES)
run: up
up: build
	$(CONTROL) up
test: build check-controls
	$(CONTROL) test
check-controls:
	python3 -m unittest test_controls.py test_replica_proofs.py
benchmark: build
	$(CONTROL) benchmark
failover: build
	$(CONTROL) primary-crash
replica-crash: build
	$(CONTROL) replica-crash
disk-failure: build
	$(CONTROL) disk-failure
resume-disk-recovery:
	$(CONTROL) resume-disk-recovery
rebuilt-crash: build
	$(CONTROL) rebuilt-crash
cold-reopen: build
	$(CONTROL) cold-reopen
crash-reopen: build
	$(CONTROL) crash-reopen
test-production:
	$(MAKE) test
ifeq ($(MODE),cluster)
	$(MAKE) failover
	$(MAKE) replica-crash
	$(MAKE) disk-failure
	$(MAKE) rebuilt-crash
endif
	$(MAKE) cold-reopen
	$(MAKE) crash-reopen
	$(MAKE) test
status:
	$(COMPOSE) ps --all
	$(CONTROL) state
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(CONTROL) down
docker-down:
	$(CONTROL) docker-down
