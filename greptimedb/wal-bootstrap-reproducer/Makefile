export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
ROWS ?= 100
CONTROL ?= 0
export ROWS CONTROL
.NOTPARALLEL:
.PHONY: docker-up up test reproduce repair bootstrap-check status down docker-down

docker-up:
	python3 scripts/control.py infrastructure
up:
	python3 scripts/control.py seed
test:
	docker compose run --rm -T wal-workload verify
reproduce:
	python3 scripts/control.py restart
repair:
	docker compose run --rm -T wal-workload repair
	$(MAKE) test
bootstrap-check:
	python3 scripts/control.py guard
status:
	docker compose ps
down:
	docker compose stop front data meta
docker-down:
	docker compose --profile tools down --volumes --remove-orphans
	@if docker image inspect greptime-wal-bootstrap-workload:dev >/dev/null 2>&1; then docker image rm greptime-wal-bootstrap-workload:dev; fi
