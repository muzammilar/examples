SHELL := /bin/sh
COMPOSE_PROJECT_NAME := starrocks-single
COUNT ?= 1000000
FAULT_BATCH_ROWS ?= 1000
FAULT_PAUSE_MS ?= 1000
export COMPOSE_PROJECT_NAME COUNT FAULT_BATCH_ROWS FAULT_PAUSE_MS
COMPOSE := docker compose -f docker-compose.json
RUN := $(COMPOSE) run --rm workload
.NOTPARALLEL:
.PHONY: docker-up up test benchmark status down docker-down
docker-up:
	python3 lifecycle.py $(COMPOSE_PROJECT_NAME)
	python3 prepare.py mutable-orders docker
	python3 config.py
	$(COMPOSE) build sr-fe-0 sr-be-0 workload
	$(COMPOSE) up -d --no-deps sr-fe-0
	python3 ready.py
	$(COMPOSE) up -d --wait --wait-timeout 600
up:
	python3 ready.py all
	$(RUN) setup
	@if test -f results/seed.json; then $(RUN) verify; else $(RUN) seed; fi
test:
	python3 -c 'from pathlib import Path; import time; r=Path("results"); [(p.rename(p.with_name(p.stem+"-before-"+str(time.time_ns())+".json"))) for n in ("correctness.json", "recovery-history.json") if (p:=r/n).exists()]'
	$(RUN) verify
	python3 -c 'import json; from pathlib import Path; r=Path("results"); assert json.load(open(r/"correctness.json")); assert not list(r.glob("fault-*.jsonl")) or json.load(open(r/"recovery-history.json")), "missing fresh recovery reconciliation"'
benchmark:
	python3 -c 'from pathlib import Path; import time; p=Path("results/query-benchmark.json"); p.exists() and p.rename(p.with_name("query-benchmark-before-"+str(time.time_ns())+".json"))'
	$(RUN) benchmark
	python3 -c 'import json; d=json.load(open("results/query-benchmark.json")); assert len(d)==6 and all(v["samples"]==20 and v["errors"]==0 for v in d.values()), "incomplete benchmark results"'
	test -s results/query-benchmark.json
status:
	$(COMPOSE) ps
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	docker image rm $(COMPOSE_PROJECT_NAME)-fe:dev $(COMPOSE_PROJECT_NAME)-be:dev $(COMPOSE_PROJECT_NAME)-client:dev
	rm -rf .build config/generated docker-compose.json
