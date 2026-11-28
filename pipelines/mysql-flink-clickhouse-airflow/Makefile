SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
COMPOSE := docker compose --project-name mysql-flink-clickhouse-airflow
export COMPOSE_PROJECT_NAME := mysql-flink-clickhouse-airflow
export LAB_UID := $(shell id -u)
ORDER_COUNT ?= 1000000
CUSTOMER_COUNT ?= 10000
export ORDER_COUNT CUSTOMER_COUNT
.NOTPARALLEL:
.PHONY: check docker-up infrastructure up initialize test test-production production-cases verify status cli down remove docker-down report connector-lifecycle connector-case backfill-recovery backfill-case worker-recovery worker-case

check: ## Compile actors and validate native deployment files.
	$(PYTHON) -m unittest discover -s tests
	$(COMPOSE) config --quiet
	$(COMPOSE) build orders-workload jobmanager airflow-api
docker-up: check ## Prepare the isolated local infrastructure.
	$(PYTHON) scripts/observe.py infrastructure PYTHON=$(PYTHON)
infrastructure:
	$(PYTHON) scripts/control.py docker-up
up: ## Seed and verify source input, register CDC and submit the join.
	$(PYTHON) scripts/observe.py initialize PYTHON=$(PYTHON)
initialize:
	$(PYTHON) scripts/control.py up
.PHONY: resume-submission resume-sql
resume-submission: ## Resume failed SQL submission over acknowledged seed data.
	$(PYTHON) scripts/observe.py resume-sql PYTHON=$(PYTHON)
resume-sql:
	$(PYTHON) scripts/control.py resume-submission
test: test-production ## Run the focused serial correctness/recovery cases.
test-production:
	$(PYTHON) scripts/observe.py production-cases PYTHON=$(PYTHON)
production-cases:
	$(PYTHON) scripts/control.py test
connector-lifecycle: ## Pause/resume Connect under live source writes and reads.
	$(PYTHON) scripts/observe.py connector-case PYTHON=$(PYTHON)
connector-case:
	$(PYTHON) scripts/control.py connector-lifecycle
backfill-recovery: ## Recover an interrupted incremental snapshot after additive DDL.
	$(PYTHON) scripts/observe.py backfill-case PYTHON=$(PYTHON)
backfill-case:
	$(PYTHON) scripts/control.py backfill-recovery
worker-recovery: ## Crash one TaskManager and verify checkpoint recovery.
	$(PYTHON) scripts/observe.py worker-case PYTHON=$(PYTHON)
worker-case:
	$(PYTHON) scripts/control.py worker-recovery
verify: ## Reconcile existing acknowledged data; never reset it.
	$(PYTHON) scripts/observe.py verify-retained PYTHON=$(PYTHON)
.PHONY: verify-retained
verify-retained:
	$(PYTHON) scripts/control.py verify
status: ## Show this example's roles only.
	$(COMPOSE) ps --all
cli:
	$(COMPOSE) exec ch1 clickhouse-client --user lab --password local-control-only
down: ## Verify preserved history and remove application state.
	$(PYTHON) scripts/observe.py remove PYTHON=$(PYTHON)
remove:
	$(PYTHON) scripts/control.py down
docker-down: ## Destroy verified example-owned infrastructure.
	$(PYTHON) scripts/control.py docker-down
report:
	$(PYTHON) scripts/curate.py
