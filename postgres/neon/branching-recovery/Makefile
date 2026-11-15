SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= neon-branching
ROWS ?= 1000000
FAULT_SERVICE ?= sk0
RESULTS_DIR := $(CURDIR)/results
GARAGE_CONFIG_DIR := $(CURDIR)/config/generated
S3_ACCESS_KEY := GK0123456789abcdef01234567
S3_SECRET_KEY := local-only-object-storage-secret-key
export COMPOSE_PROJECT_NAME ROWS FAULT_SERVICE RESULTS_DIR GARAGE_CONFIG_DIR S3_ACCESS_KEY S3_SECRET_KEY
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm runner
.NOTPARALLEL:
.PHONY: dependencies docker-up up test benchmark branch failover disk-failure chaos-compute compute-replacement test-production status cli down docker-down
dependencies:
	python3 lifecycle.py $(COMPOSE_PROJECT_NAME)
	python3 prepare.py orders-workload
	python3 scripts/garage_config.py
	mkdir -p results/generated
	$(COMPOSE) build runner
	$(COMPOSE) up -d --wait garage0 garage1 garage2 garage3 garage4 garage5 garage6 garage7 garage8 s3
	python3 scripts/garage_bootstrap.py
	$(COMPOSE) up -d --wait pg0 pg1 pg2
	$(COMPOSE) exec -T pg0 psql -U postgres -v ON_ERROR_STOP=1 -c "ALTER SYSTEM SET synchronous_standby_names = 'ANY 1 (pg1,pg2)'"
	$(COMPOSE) exec -T pg0 psql -U postgres -v ON_ERROR_STOP=1 -c 'SELECT pg_reload_conf()'
	$(RUN) wait-baseline
docker-up: dependencies
	$(COMPOSE) up -d --wait broker pageserver sk0 sk1 sk2
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
branch:
	$(RUN) branch
	$(COMPOSE) --profile workload up -d --wait branch
	$(RUN) verify-branch
failover:
	python3 scripts/fault.py process
disk-failure:
	python3 scripts/fault.py disk
chaos-compute:
	python3 scripts/fault.py compute
compute-replacement:
	python3 scripts/fault.py cache
test-production:
	$(MAKE) test
	$(MAKE) branch
	$(MAKE) failover
	$(MAKE) disk-failure
	$(MAKE) failover FAULT_SERVICE=sk1
	$(MAKE) chaos-compute
	$(MAKE) compute-replacement
	$(MAKE) test
status:
	$(COMPOSE) --profile workload ps
cli:
	$(COMPOSE) exec compute psql -p 55433 -U cloud_admin postgres
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools --profile workload down --volumes --remove-orphans
	@for image in '$(COMPOSE_PROJECT_NAME)-runner:dev' '$(COMPOSE_PROJECT_NAME)-tools:dev'; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
	rm -rf config/generated

# Local build context is generated from the tracked source link.
clean-build:
	rm -rf .build
