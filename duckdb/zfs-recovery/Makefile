export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE := docker compose
LOCAL_UID := $(shell id -u)
LOCAL_GID := $(shell id -g)
export LOCAL_UID LOCAL_GID
DISK_GIB ?= 16
BENCH_ROWS ?= 200000
QUERY_RUNS ?= 20
export DISK_GIB BENCH_ROWS QUERY_RUNS
ROWS ?= 2000000
CUSTOMERS ?= 10000
BATCH_ROWS ?= 10000
CLIENTS ?= 4
BATCH_DELAY_MS ?= 1000
LIVE_BATCH_ROWS ?= 1000
LIVE_WRITE_DELAY_S ?= 0.5
FAILED_SUITE ?=
LIVE_CASE ?=
RESULTS_DIR ?= results/manual
export ROWS CUSTOMERS BATCH_ROWS CLIENTS BATCH_DELAY_MS RESULTS_DIR
export LIVE_BATCH_ROWS LIVE_WRITE_DELAY_S
RUN := $(COMPOSE) run --rm --name duckdb-zfs-recovery-controller controller
.NOTPARALLEL:
.PHONY: docker-up up test disk-test crash-test replica-test benchmark status cli down docker-down keys check-controls test-production report-production verify-archives check-native-export check-native-fencing retire-failed-suite
keys:
	@mkdir -p state/ssh "$(RESULTS_DIR)"
	@test -f state/ssh/id_ed25519 || ssh-keygen -q -t ed25519 -N '' -f state/ssh/id_ed25519

docker-up: keys
	$(COMPOSE) build primary
	$(COMPOSE) up -d --wait primary standby

up:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) up

test: check-controls check-native-export check-native-fencing
	python3 scripts/production.py full-test

verify-archives:
	python3 scripts/production.py verify-archives

disk-test:
	@mkdir -p "$(RESULTS_DIR)"
	python3 scripts/production.py register-fixture
	$(RUN) disk-test

crash-test:
	@mkdir -p "$(RESULTS_DIR)"
	python3 scripts/production.py crash

check-controls:
	python3 -m unittest discover -s app -p 'test_*.py'
	python3 -m unittest test_production

check-native-export:
	$(COMPOSE) run --rm --entrypoint /opt/venv/bin/python controller /opt/lab/app/export_control.py

check-native-fencing:
	$(COMPOSE) run --rm --entrypoint /opt/venv/bin/python controller /opt/lab/app/fencing_control.py

retire-failed-suite:
	@test -n "$(FAILED_SUITE)" || { echo 'Set FAILED_SUITE=results/<run>/full-suite-<id>'; exit 1; }
	python3 scripts/production.py retire-failed-suite "$(FAILED_SUITE)"

test-production: test

report-production:
	@test -n "$(LIVE_CASE)" || { echo 'Set LIVE_CASE=results/<run>/live/<case>'; exit 1; }
	python3 scripts/production.py report "$(LIVE_CASE)"

replica-test:
	@mkdir -p "$(RESULTS_DIR)"
	python3 scripts/production.py register-fixture
	$(COMPOSE) up -d --wait standby
	$(RUN) replica-test

benchmark:
	@mkdir -p "$(RESULTS_DIR)"
	python3 scripts/production.py register-fixture
	$(RUN) benchmark
	$(MAKE) verify-archives

status:
	$(COMPOSE) ps --all
	$(RUN) status

cli:
	$(COMPOSE) exec primary ssh -i /keys/id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 2222 lab@127.0.0.1

down:
	$(RUN) down

docker-down:
	python3 scripts/production.py teardown-guard
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@if docker image inspect duckdb-zfs-lab:dev >/dev/null 2>&1; then docker image rm duckdb-zfs-lab:dev; fi
	python3 scripts/production.py verify-teardown
	@rm -f state/ssh/id_ed25519 state/ssh/id_ed25519.pub
	@rmdir state/ssh state 2>/dev/null || true
