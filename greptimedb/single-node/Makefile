export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= greptime-single
SERIES ?= 10000
POINTS ?= 100
LOGS ?= 1000000
export COMPOSE_PROJECT_NAME SERIES POINTS LOGS
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm client
.NOTPARALLEL:
.PHONY: docker-up up test benchmark status cli down docker-down
docker-up:
	python3 scripts/lifecycle.py $(COMPOSE_PROJECT_NAME)
	python3 scripts/prepare.py telemetry-workload
	mkdir -p results
	$(COMPOSE) build client vlogs
	$(COMPOSE) up -d --wait greptime victoria vlogs
up:
	$(RUN) seed
test:
	python3 -c 'from pathlib import Path; import time; r=Path("results"); [(p.rename(p.with_name(p.stem+"-before-"+str(time.time_ns())+".json"))) for n in ("correctness.json", "recovery-history.json") if (p:=r/n).exists()]'
	$(RUN) verify
	python3 -c 'import json; from pathlib import Path; r=Path("results"); assert json.load(open(r/"correctness.json")); assert not list(r.glob("fault-*.jsonl")) or json.load(open(r/"recovery-history.json")), "missing fresh recovery reconciliation"'
benchmark:
	python3 -c 'from pathlib import Path; import time; p=Path("results/query-benchmark.json"); p.exists() and p.rename(p.with_name("query-benchmark-before-"+str(time.time_ns())+".json"))'
	$(RUN) benchmark
	python3 -c 'import json; d=json.load(open("results/query-benchmark.json")); assert len(d)==6 and all(v["requests"]==20 and v["request_errors"]==0 for v in d.values()), "incomplete benchmark results"'
status:
	$(COMPOSE) ps
cli:
	$(COMPOSE) exec greptime curl -fsS --get --data-urlencode 'sql=SHOW TABLES' http://localhost:4000/v1/sql
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@for image in '$(COMPOSE_PROJECT_NAME)-client:dev' '$(COMPOSE_PROJECT_NAME)-vlogs:dev'; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done

# Local build context is generated from the tracked source link.
clean-build:
	rm -rf .build
