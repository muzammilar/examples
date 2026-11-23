export PYTHONPATH := $(CURDIR)/scripts:$(CURDIR)/tests
SHELL := /bin/sh
COMPOSE := docker compose --project-name postgres-pgdog-pooling
COUNT ?= 1000000
CUSTOMERS ?= 10000
WORKERS ?= 8
BATCH ?= 100
CHAOS_RATE ?= 25000
POOL_SIZE ?= 16
BURST_CLIENTS ?= 256
BURST_REQUESTS ?= 4000
export COUNT CUSTOMERS WORKERS BATCH CHAOS_RATE POOL_SIZE BURST_CLIENTS BURST_REQUESTS
.NOTPARALLEL:
.PHONY: docker-up render-config images up test verify benchmark failover chaos-replica-lag replica-crash router-failure pool-reload test-production unit-test status cli config pool-size compare-pools replica-reads connection-burst down docker-down

docker-up: render-config
	$(COMPOSE) up --build -d --wait --wait-timeout 300
render-config:
	python3 scripts/render-config.py
images:
	$(COMPOSE) build workload
up: images
	python3 scripts/control.py up
test:
	python3 scripts/control.py test
	$(MAKE) connection-burst
verify:
	python3 scripts/control.py verify
benchmark:
	python3 scripts/control.py benchmark
failover:
	python3 scripts/control.py failover
chaos-replica-lag:
	python3 scripts/control.py replica-lag
replica-crash:
	python3 scripts/control.py replica-crash
router-failure:
	python3 scripts/control.py router
pool-reload:
	python3 scripts/control.py pool-reload
test-production:
	$(MAKE) test
	$(MAKE) failover
	$(MAKE) chaos-replica-lag
	$(MAKE) replica-crash
	$(MAKE) router-failure
	$(MAKE) pool-reload
	$(MAKE) replica-reads
	$(MAKE) verify
unit-test:
	$(COMPOSE) build workload
	python3 -m unittest discover -s tests -p 'test_*.py'
status:
	$(COMPOSE) ps
	python3 scripts/control.py status
cli:
	$(COMPOSE) run --rm client psql -h pgdog -p 6432 -U postgres -d orders
config:
	python3 scripts/control.py config
pool-size:
	python3 scripts/control.py pool-size
compare-pools:
	python3 scripts/control.py compare-pools
replica-reads:
	python3 scripts/control.py replica-reads
connection-burst:
	python3 scripts/control.py connection-burst
down:
	python3 scripts/control.py down
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local
	@if docker image inspect postgres-pgdog-workload:dev >/dev/null 2>&1; then docker image rm postgres-pgdog-workload:dev; fi
	@if docker image inspect postgres-pgdog-patroni:dev >/dev/null 2>&1; then docker image rm postgres-pgdog-patroni:dev; fi
