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
ifeq ($(MODE),single)
URLS := http://writer:8080
SERVICES := writer
else
URLS := http://reader1:8080,http://reader2:8080,http://reader3:8080
SERVICES := writer reader1 reader2 reader3
endif
COMPOSE_FILE := $(CURDIR)/docker-compose.yml
COMPOSE_PROJECT_NAME := lancedb-versioned-product-search
export MODE RESULTS_DIR DOCUMENTS QUERIES SEED DURATION_SECONDS UPDATE_BATCH NODE_DOWN_SECONDS URLS COMPOSE_FILE COMPOSE_PROJECT_NAME
COMPOSE := docker compose --file "$(COMPOSE_FILE)" --project-name $(COMPOSE_PROJECT_NAME) --profile $(MODE)
CONTROL := python3 control.py
.NOTPARALLEL:
.PHONY: corpus build docker-up up test check-controls failover writer-crash reader-crash maintenance rolling-restart cold-reopen crash-reopen test-production benchmark status cli down docker-down
corpus:
	python3 corpus.py
build:
	python3 prepare_build.py
	$(COMPOSE) build tools inventory-workload
docker-up: corpus build
	$(CONTROL) preflight
	@mkdir -p "$(RESULTS_DIR)"
	$(COMPOSE) up -d --wait $(SERVICES)
up: build
	$(CONTROL) up
test: build check-controls
	$(CONTROL) test
check-controls:
	python3 -m unittest test_controls.py
failover: writer-crash
writer-crash: build
	$(CONTROL) writer-crash
reader-crash: build
	$(CONTROL) reader-crash
maintenance: build
	$(CONTROL) maintenance
rolling-restart: build
	$(CONTROL) rolling-restart
cold-reopen: build
	$(CONTROL) cold-reopen
crash-reopen: build
	$(CONTROL) crash-reopen
test-production:
	$(MAKE) test
	$(MAKE) writer-crash
ifeq ($(MODE),cluster)
	$(MAKE) reader-crash
endif
	$(MAKE) maintenance
	$(MAKE) rolling-restart
	$(MAKE) cold-reopen
	$(MAKE) crash-reopen
	$(MAKE) test
benchmark: build
	$(CONTROL) benchmark
status:
	$(COMPOSE) ps --all
	$(CONTROL) state
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(CONTROL) down
docker-down:
	$(CONTROL) docker-down
