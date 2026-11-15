SHELL := /bin/sh
MODE ?= cluster
ifeq ($(filter $(MODE),single cluster sharded),)
$(error MODE must be single, cluster or sharded)
endif
RESULTS_DIR ?= results/$(MODE)
DOCUMENTS ?= 100000
QUERIES ?= 1000
export MODE RESULTS_DIR DOCUMENTS QUERIES
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
COMPOSE := docker compose $(PROFILE)
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
.NOTPARALLEL:
.PHONY: build run config corpus docker-up up test benchmark failover status cli down docker-down
config:
	python3 scripts/config.py
	@mkdir -p "$(RESULTS_DIR)"
corpus:
	$(MAKE) -C ../../datasets/esci download prepare
docker-up: config
	$(COMPOSE) build ts1
	$(COMPOSE) up -d --wait $(SERVICES)
build:
	$(COMPOSE) build tools
run:
	$(RUN) load
up: config corpus build
	$(RUN) load
test:
ifeq ($(MODE),sharded)
	$(RUN) routing-test
endif
	$(RUN) test
benchmark:
	python3 ../../datasets/esci/client/bench.py
failover:
	ENGINE=typesense python3 ../../datasets/esci/client/fault.py
status:
	$(COMPOSE) ps --all
	$(RUN) stats
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(COMPOSE) rm -sfv $(SERVICES)
docker-down:
	docker compose --profile cluster --profile sharded --profile tools down --volumes --remove-orphans
	@for image in typesense-product-tools:dev typesense-products-server:dev; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
