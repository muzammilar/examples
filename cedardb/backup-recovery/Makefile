SHELL := /bin/sh
ROWS ?= 1000000
RATE ?= 50
TRIALS ?= 3
ANALYTICS_WARMUP_S ?= 60
ANALYTICS_DURATION_S ?= 300
HTAP_RESULTS_DIR ?=
MUTABLE_RESULTS_DIR ?=
export ROWS RATE
export TRIALS ANALYTICS_WARMUP_S ANALYTICS_DURATION_S
.NOTPARALLEL:
.PHONY: docker-up up test check-controls graceful crash backup test-production report benchmark-htap report-htap status cli down docker-down
docker-up:
	python3 control.py docker-up
up:
	python3 control.py up
check-controls:
	python3 -m unittest test_control.py test_htap.py test_htap_report.py test_mutable.py
test: check-controls
	python3 control.py test
	python3 driver_checks.py
graceful:
	python3 control.py graceful
crash:
	python3 control.py crash
backup:
	python3 control.py backup
test-production: check-controls
	python3 control.py test-production
report:
	python3 report.py
benchmark-htap:
	python3 htap.py

.PHONY: test-mutable test-mutable-recovery test-mutable-benchmark benchmark-mutable report-mutable-benchmark
test-mutable: check-controls
	python3 mutable_lab.py
test-mutable-recovery: check-controls
	python3 mutable_lab.py --crash
test-mutable-benchmark: check-controls
	TRIALS=1 ANALYTICS_WARMUP_S=2 ANALYTICS_DURATION_S=10 python3 mutable_benchmark.py
benchmark-mutable: check-controls
	python3 mutable_benchmark.py
report-mutable-benchmark:
	@test -n "$(MUTABLE_RESULTS_DIR)" || { echo 'Set MUTABLE_RESULTS_DIR=results/mutable-benchmark/<run-id>'; exit 1; }
	python3 mutable_benchmark_report.py "$(MUTABLE_RESULTS_DIR)"
report-htap:
	@test -n "$(HTAP_RESULTS_DIR)" || { echo 'Set HTAP_RESULTS_DIR=results/htap/<run-id> from the completed benchmark'; exit 1; }
	python3 htap_report.py "$(HTAP_RESULTS_DIR)"
status:
	python3 control.py status
cli:
	docker compose --profile tools run --rm sql
down:
	python3 control.py down
docker-down:
	python3 control.py docker-down
