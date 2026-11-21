SHELL := /bin/sh
ROWS ?= 1000000
RATE ?= 50
export ROWS RATE
.NOTPARALLEL:
.PHONY: docker-up up test check-controls graceful crash backup test-production report status cli down docker-down
docker-up:
	python3 control.py docker-up
up:
	python3 control.py up
check-controls:
	python3 -m unittest test_control.py
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
status:
	python3 control.py status
cli:
	docker compose --profile tools run --rm sql
down:
	python3 control.py down
docker-down:
	python3 control.py docker-down
