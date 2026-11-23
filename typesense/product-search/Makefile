export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
MODE ?= cluster
ifeq ($(filter $(MODE),single cluster sharded),)
$(error MODE must be single, cluster or sharded)
endif
RESULTS_DIR ?= results/production/$(MODE)
DOCUMENTS ?= 100000
QUERIES ?= 1000
export MODE RESULTS_DIR DOCUMENTS QUERIES
SEED ?= 20261115
export SEED
ifeq ($(MODE),single)
URLS := http://ts1:8108
PROFILE :=
SERVICES := ts1
else ifeq ($(MODE),sharded)
URLS := http://ts1:8108,http://ts2:8108,http://ts3:8108
PROFILE := --profile sharded
SERVICES := ts1 ts2 ts3 ts4 ts5 ts6 ts7 ts8 ts9
else
URLS := http://ts1:8108,http://ts2:8108,http://ts3:8108
PROFILE := --profile cluster
SERVICES := ts1 ts2 ts3
endif
export URLS
COMPOSE := docker compose --project-name typesense-product-search $(PROFILE)
CONTROL := python3 scripts/control.py
HEALTHY_READ_LAG ?= 10
UPDATE_BATCH ?= 10
DURATION_SECONDS ?= 1800
FAULT_SHARD ?= 0
export HEALTHY_READ_LAG UPDATE_BATCH DURATION_SECONDS FAULT_SHARD
.NOTPARALLEL:
.PHONY: build run config corpus docker-up up test verify benchmark failover follower-crash disk-failure rebuilt-crash recover-disk cold-reopen report check-controls test-production status cli down docker-down
config:
	python3 scripts/config.py
	@mkdir -p "$(RESULTS_DIR)"
corpus:
	python3 scripts/corpus.py
build:
	python3 scripts/prepare_build.py
	$(COMPOSE) build ts1 tools inventory-workload
docker-up:
	$(CONTROL) docker-up
up run:
	$(CONTROL) up
test:
	$(CONTROL) test
verify:
	$(CONTROL) verify
benchmark:
	$(CONTROL) benchmark
failover:
	$(CONTROL) leader
follower-crash:
	$(CONTROL) follower
disk-failure:
	$(CONTROL) disk
rebuilt-crash:
	$(CONTROL) rebuilt
recover-disk:
	$(CONTROL) recover-disk
cold-reopen:
	$(CONTROL) cold-reopen
test-production:
	$(MAKE) test
ifeq ($(MODE),single)
	$(MAKE) cold-reopen
else ifeq ($(MODE),sharded)
	@for shard in 0 1 2; do \
		$(MAKE) FAULT_SHARD=$$shard failover && \
		$(MAKE) FAULT_SHARD=$$shard follower-crash && \
		$(MAKE) FAULT_SHARD=$$shard disk-failure && \
		$(MAKE) FAULT_SHARD=$$shard rebuilt-crash || exit $$?; \
	done
else
	$(MAKE) failover
	$(MAKE) follower-crash
	$(MAKE) disk-failure
	$(MAKE) rebuilt-crash
endif
	$(MAKE) test
report:
	python3 scripts/report.py
check-controls:
	python3 -m unittest test_controls
status:
	$(COMPOSE) ps --all
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(CONTROL) down
docker-down:
	$(CONTROL) docker-down
