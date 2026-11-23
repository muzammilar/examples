export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE_PROJECT_NAME := qdrant-turboquant
POINTS ?= 100000
DIMENSIONS ?= 384
QUERIES ?= 100
HNSW_EF ?= 128
CONCURRENCY ?= 4
DURATION ?= 10
FAULT_SERVICE ?=
export COMPOSE_PROJECT_NAME POINTS DIMENSIONS QUERIES HNSW_EF CONCURRENCY DURATION FAULT_SERVICE
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm vector-workload
.NOTPARALLEL:
.PHONY: docker-up up test benchmark failover write-leader-failure disk-failure recover test-production status down docker-down
docker-up:
	python3 scripts/lifecycle.py $(COMPOSE_PROJECT_NAME)
	mkdir -p results
	$(COMPOSE) build q0 vector-workload
	$(COMPOSE) up -d --wait --wait-timeout 300 q0 q1 q2
	$(RUN) ready
up:
	$(RUN) setup
	@if test -f results/seed.json; then $(RUN) verify; else $(RUN) seed; fi
test:
	python3 -c 'from pathlib import Path; import time; r=Path("results"); [(p.rename(p.with_name(p.stem+"-before-"+str(time.time_ns())+".json"))) for n in ("physical-placement.json", "recovery-history.json") if (p:=r/n).exists()]'
	$(RUN) ready
	$(RUN) verify
	python3 -c 'import json; from pathlib import Path; r=Path("results"); h=json.load(open(r/"recovery-history.json")); assert h["full_baseline"] and h["missing_acknowledged"]==0 and h["wrong_values_or_routing"]==0 and len(json.load(open(r/"physical-placement.json")))==36, "incomplete physical-copy verification"'
benchmark:
	python3 scripts/run_benchmark.py
write-leader-failure:
	python3 scripts/fault.py write-leader
failover:
	python3 scripts/fault.py process
disk-failure:
	python3 scripts/fault.py disk
recover:
	python3 scripts/fault.py recover
test-production:
	$(MAKE) test
	$(MAKE) failover
	$(MAKE) write-leader-failure
	$(MAKE) disk-failure
	$(MAKE) failover FAULT_SERVICE=q1
	$(MAKE) test
status:
	$(COMPOSE) ps
	$(RUN) api q0 GET /cluster null
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	docker image rm $(COMPOSE_PROJECT_NAME)-workload:dev $(COMPOSE_PROJECT_NAME)-server:dev
