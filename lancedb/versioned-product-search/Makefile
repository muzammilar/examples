SHELL := /bin/sh
MODE ?= cluster
ifeq ($(filter $(MODE),single cluster),)
$(error MODE must be single or cluster)
endif
RESULTS_DIR ?= results/$(MODE)
DOCUMENTS ?= 100000
QUERIES ?= 1000
export MODE RESULTS_DIR DOCUMENTS QUERIES
ifeq ($(MODE),single)
URLS := http://writer:8080
SERVICES := writer
else
URLS := http://reader1:8080,http://reader2:8080,http://reader3:8080
SERVICES := writer reader1 reader2 reader3
endif
export URLS
COMPOSE := docker compose --profile cluster
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
.NOTPARALLEL:
.PHONY: run corpus build docker-up up test benchmark failover maintenance status cli down docker-down
corpus:
	$(MAKE) -C ../../datasets/esci download prepare
build:
	$(COMPOSE) build writer
docker-up: corpus build
	@mkdir -p "$(RESULTS_DIR)"
	$(COMPOSE) up -d --wait $(SERVICES)
run:
	$(RUN) load
up: run
test:
	$(RUN) test
benchmark:
	python3 ../../datasets/esci/client/bench.py
failover:
	ENGINE=lancedb python3 ../../datasets/esci/client/fault.py
maintenance:
	$(RUN) maintenance
status:
	$(COMPOSE) ps --all
	$(RUN) stats
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(COMPOSE) rm -sfv $(SERVICES)
docker-down:
	docker compose --profile cluster --profile tools down --volumes --remove-orphans
	@if docker image inspect lancedb-product-tools:dev >/dev/null 2>&1; then docker image rm lancedb-product-tools:dev; fi
