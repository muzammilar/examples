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
PG_NODES := single
SERVICES := single
else
PG_NODES := pg0,pg1,pg2
SERVICES := etcd0 etcd1 etcd2 pg0 pg1 pg2
endif
export PG_NODES
COMPOSE := docker compose --profile $(MODE)
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) tools
.NOTPARALLEL:
.PHONY: build run corpus docker-up up test benchmark failover status cli down docker-down
corpus:
	$(MAKE) -C ../../datasets/esci download prepare
docker-up:
	@mkdir -p "$(RESULTS_DIR)"
ifeq ($(MODE),cluster)
	$(COMPOSE) build pg0
endif
	$(COMPOSE) up -d --wait $(SERVICES)
build:
	$(COMPOSE) build tools
run:
	$(RUN) load
up: corpus build
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) load
test:
	$(RUN) test
benchmark:
	python3 ../../datasets/esci/client/bench.py
failover:
	ENGINE=pgvector python3 ../../datasets/esci/client/fault.py
status:
	$(COMPOSE) ps --all
	$(RUN) stats
cli:
	$(COMPOSE) run --rm --entrypoint /bin/sh tools
down:
	$(COMPOSE) rm -sfv pg0 pg1 pg2 single
docker-down:
	docker compose --profile single --profile cluster --profile tools down --volumes --remove-orphans
	@for image in pgvector-product-tools:dev pgvector-products-patroni:dev; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
