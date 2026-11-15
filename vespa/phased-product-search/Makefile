SHELL := /bin/sh
MODE ?= cluster
ifeq ($(filter $(MODE),single cluster sharded),)
$(error MODE must be single, cluster or sharded)
endif
RESULTS_DIR ?= results/$(MODE)
DOCUMENTS ?= 100000
QUERIES ?= 1000
ifeq ($(MODE),sharded)
DURATION ?= 120
NODE_DOWN_SECONDS ?= 60
CONTROL_MEMORY := 2g
IGNORE_MEMORY_FLOOR := true
else
DURATION ?= 300
NODE_DOWN_SECONDS ?= 180
CONTROL_MEMORY := 4g
IGNORE_MEMORY_FLOOR := false
endif
export CONTROL_MEMORY IGNORE_MEMORY_FLOOR
export MODE RESULTS_DIR DOCUMENTS QUERIES DURATION NODE_DOWN_SECONDS
ifeq ($(MODE),single)
URLS := http://node0:8080
PROFILE :=
SERVICES := node0
CONFIGSERVERS := vespa-products-node0.vespa-product-search-net
else
URLS := http://node0:8080,http://node1:8080,http://node2:8080
PROFILE := --profile cluster
SERVICES := node0 node1 node2
CONFIGSERVERS := vespa-products-node0.vespa-product-search-net,vespa-products-node1.vespa-product-search-net,vespa-products-node2.vespa-product-search-net
endif
ifeq ($(MODE),sharded)
PROFILE := --profile sharded
SERVICES += content0 content1 content2 content3 content4 content5 content6 content7 content8
endif
export URLS CONFIGSERVERS
COMPOSE := docker compose $(PROFILE)
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
.NOTPARALLEL:
.PHONY: build run config corpus docker-up up deploy test benchmark failover status cli down docker-down
config:
	python3 scripts/config.py
	@mkdir -p "$(RESULTS_DIR)"
corpus:
	$(MAKE) -C ../../datasets/esci download prepare
docker-up: config
	$(COMPOSE) up -d --wait $(SERVICES)
build:
	$(COMPOSE) build tools
run:
	$(RUN) load
up: config corpus build
	$(RUN) load
deploy: config build
	$(RUN) deploy
test:
	$(RUN) copy-contract-test
	$(RUN) test
	python3 scripts/copies.py baseline
benchmark:
	python3 ../../datasets/esci/client/bench.py
failover:
	ENGINE=vespa python3 ../../datasets/esci/client/fault.py
status:
	$(COMPOSE) ps --all
	$(RUN) stats
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(COMPOSE) rm -sfv $(SERVICES)
docker-down:
	docker compose --profile cluster --profile sharded --profile tools down --volumes --remove-orphans
	@for image in vespa-product-tools:dev; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
