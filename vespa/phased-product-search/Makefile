SHELL := /bin/sh
MODE ?= cluster
ifeq ($(filter $(MODE),single cluster sharded),)
$(error MODE must be single, cluster or sharded)
endif
RESULTS_DIR ?= results/production/$(MODE)
DOCUMENTS ?= 100000
QUERIES ?= 1000
SEED ?= 20261115
DURATION_SECONDS ?= 1800
UPDATE_BATCH ?= 10
UPDATE_CONCURRENCY ?= 4
ifeq ($(MODE),sharded)
NODE_DOWN_SECONDS ?= 60
CONTROL_MEMORY := 2g
IGNORE_MEMORY_FLOOR := true
else
NODE_DOWN_SECONDS ?= 180
CONTROL_MEMORY := 4g
IGNORE_MEMORY_FLOOR := false
endif
ifeq ($(MODE),single)
CONFIGSERVERS := vespa-products-node0.vespa-product-search-net
else
CONFIGSERVERS := vespa-products-node0.vespa-product-search-net,vespa-products-node1.vespa-product-search-net,vespa-products-node2.vespa-product-search-net
endif
export MODE RESULTS_DIR DOCUMENTS QUERIES SEED DURATION_SECONDS UPDATE_BATCH UPDATE_CONCURRENCY
export NODE_DOWN_SECONDS CONTROL_MEMORY IGNORE_MEMORY_FLOOR CONFIGSERVERS
CONTROL := python3 control.py
.NOTPARALLEL:
.PHONY: config corpus build docker-up up test check-controls failover replica-crash disk-failure rebuilt-crash test-production cold-reopen report benchmark status down docker-down
config:
	python3 scripts/config.py
corpus:
	python3 corpus.py
build:
	python3 prepare_build.py
	docker compose build tools inventory-workload
docker-up: config
	$(CONTROL) docker-up
up:
	$(CONTROL) up
test: check-controls
	$(CONTROL) test
check-controls:
	python3 -m unittest test_controls.py
failover:
	$(CONTROL) crash
replica-crash:
	FAULT_SERVICE=replica $(CONTROL) crash
disk-failure:
	$(CONTROL) disk
rebuilt-crash:
	$(CONTROL) rebuilt-crash
test-production:
	$(MAKE) test
ifeq ($(MODE),single)
	$(MAKE) cold-reopen
else
	$(MAKE) failover
	$(MAKE) replica-crash
	$(MAKE) disk-failure
	$(MAKE) rebuilt-crash
	$(MAKE) cold-reopen
endif
	$(MAKE) test
cold-reopen:
	$(CONTROL) cold-reopen
report:
	python3 report.py
benchmark:
	$(CONTROL) benchmark
status:
	$(CONTROL) state
# Native application teardown and infrastructure teardown are separate operations.
down:
	$(CONTROL) down
docker-down:
	$(CONTROL) docker-down
