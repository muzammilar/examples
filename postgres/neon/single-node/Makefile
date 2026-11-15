SHELL := /bin/sh
COMPOSE_PROJECT_NAME := neon-single
ROWS ?= 1000000
export COMPOSE_PROJECT_NAME ROWS
COMPOSE := docker compose -f docker-compose.json
RUN := $(COMPOSE) run --rm runner
.NOTPARALLEL:
.PHONY: docker-up up test benchmark status cli down docker-down
docker-up:
	python3 lifecycle.py $(COMPOSE_PROJECT_NAME)
	python3 prepare.py orders-workload
	python3 config.py
	$(COMPOSE) build runner
	$(COMPOSE) up -d --wait broker pageserver sk0 pg0
up:
	$(RUN) init
	$(COMPOSE) --profile workload up -d --wait compute
	@if test -f results/seed.json; then $(RUN) verify; else $(RUN) seed; fi
test:
	python3 -c 'from pathlib import Path; import time; r=Path("results"); [(p.rename(p.with_name(p.stem+"-before-"+str(time.time_ns())+".json"))) for n in ("correctness.json", "recovery-history.json") if (p:=r/n).exists()]'
	$(RUN) verify
	python3 -c 'import json; from pathlib import Path; r=Path("results"); assert json.load(open(r/"correctness.json")); assert not list(r.glob("fault-*.jsonl")) or json.load(open(r/"recovery-history.json")), "missing fresh recovery reconciliation"'
benchmark:
	python3 -c 'from pathlib import Path; import time; p=Path("results/query-benchmark.json"); p.exists() and p.rename(p.with_name("query-benchmark-before-"+str(time.time_ns())+".json"))'
	$(RUN) benchmark
	python3 -c 'import json; d=json.load(open("results/query-benchmark.json")); assert len(d)==4 and all(v["samples"]==20 and v["errors"]==0 for v in d.values()), "incomplete benchmark results"'
status:
	$(COMPOSE) --profile workload ps
cli:
	$(COMPOSE) exec compute psql -p 55433 -U cloud_admin postgres
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools --profile workload down --volumes --remove-orphans
	docker image rm neon-single-runner:dev
	rm -rf config/generated docker-compose.json

# Local build context is generated from the tracked source link.
clean-build:
	rm -rf .build
