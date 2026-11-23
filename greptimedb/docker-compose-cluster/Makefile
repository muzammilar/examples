export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= greptime-telemetry
RESULTS_DIR := $(CURDIR)/results
GARAGE_CONFIG_DIR := $(CURDIR)/config/generated
S3_ACCESS_KEY := GK0123456789abcdef01234567
S3_SECRET_KEY := local-only-object-storage-secret-key
SERIES ?= 10000
POINTS ?= 100
LOGS ?= 1000000
export COMPOSE_PROJECT_NAME RESULTS_DIR GARAGE_CONFIG_DIR S3_ACCESS_KEY S3_SECRET_KEY SERIES POINTS LOGS
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm client
.NOTPARALLEL:
.PHONY: config docker-up up test benchmark failover disk-failure scale-out scale-in test-production status cli down docker-down
config:
	python3 scripts/garage_config.py
	mkdir -p results
dependencies: config
	python3 scripts/lifecycle.py $(COMPOSE_PROJECT_NAME)
	python3 scripts/prepare.py telemetry-workload
	$(COMPOSE) build client vlogs
	$(COMPOSE) up -d --wait garage0 garage1 garage2 garage3 garage4 garage5 garage6 garage7 garage8 s3 meta-store0 meta-store1 meta-store2 wal0 wal1 wal2
	python3 scripts/garage_bootstrap.py
	$(COMPOSE) exec -T wal0 rpk cluster config set write_caching_default false
	$(COMPOSE) exec -T wal0 rpk cluster config set default_topic_replications 3
	$(COMPOSE) exec -T wal0 rpk cluster config set default_topic_partitions 1
	$(COMPOSE) up -d --wait vmstore0 vmstore1 vmstore2 vminsert vmselect vlogs
docker-up: dependencies
	$(COMPOSE) up -d --wait meta0 meta1 meta2
	$(COMPOSE) up -d --wait data0 data1 data2 front0 front1 vmstore0 vmstore1 vmstore2 vminsert vmselect vlogs
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
failover:
	python3 scripts/fault.py process
disk-failure:
	python3 scripts/fault.py disk
scale-out:
	python3 scripts/scale.py out
scale-in:
	python3 scripts/scale.py in
test-production:
	$(MAKE) test
	$(MAKE) failover
	$(MAKE) disk-failure
	$(MAKE) scale-out
	$(MAKE) scale-in
	$(MAKE) test
status:
	$(COMPOSE) ps
cli:
	$(COMPOSE) exec front0 curl -fsS --get --data-urlencode 'sql=SHOW TABLES' http://localhost:4000/v1/sql
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools --profile scale down --volumes --remove-orphans
	@for image in '$(COMPOSE_PROJECT_NAME)-client:dev' '$(COMPOSE_PROJECT_NAME)-tools:dev' '$(COMPOSE_PROJECT_NAME)-vlogs:dev'; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
	rm -rf config/generated

# Local build context is generated from the tracked source link.
clean-build:
	rm -rf .build
