SHELL := /bin/sh
COMPOSE := docker compose -f docker-compose.yml -f config/generated/cluster.yml
QUERY_RUNS ?= 20
export QUERY_RUNS
ROWS ?= 2000000
CUSTOMERS ?= 10000
BATCH_ROWS ?= 100000
TARGET_FILE_BYTES ?= 134217728
TABLE ?= lab.events
RESULTS_DIR ?= results/manual
export ROWS CUSTOMERS BATCH_ROWS TARGET_FILE_BYTES TABLE RESULTS_DIR
LOCAL_UID := $(shell id -u)
export LOCAL_UID
LOCAL_USER := $(shell id -u):$(shell id -g)
RUN := $(COMPOSE) run --rm --user $(LOCAL_USER)
.NOTPARALLEL:

.PHONY: prepare unit-test config test-case cluster-up cluster-test cluster-failure-test
.PHONY: docker-up up test probe load verify compact status cli down docker-down

docker-up: config
	@docker info >/dev/null
	$(COMPOSE) up -d --wait storage

prepare:
	python3 prepare.py

up: config prepare
	$(COMPOSE) build runner compactor ch-workload
	$(COMPOSE) up -d --wait storage clickhouse
	@mkdir -p "$(RESULTS_DIR)"

ARCH ?= all
export ARCH

unit-test: prepare
	docker run --rm --network none --cpus=2 --memory=1g --memory-swap=1g --user $(LOCAL_USER) -e GOMAXPROCS=2 -e GOCACHE=/tmp/go-cache -e GOMODCACHE=/tmp/go-mod-cache -v "$(CURDIR)/.build/clickhouse-archive-workload:/src:ro" -w /src golang:1.27.4-bookworm go test ./...

test: config unit-test
	sh scripts/test.sh "$(ARCH)"

test-case: config
ifeq ($(ARCH),duck-direct)
	$(RUN) runner load
	$(RUN) -e REPORT_NAME=before-compaction runner verify
	$(MAKE) compact
	$(RUN) -e REPORT_NAME=after-compaction runner verify
else ifeq ($(ARCH),ch-direct)
	$(RUN) ch-workload direct
	$(RUN) runner verify
	$(MAKE) compact
	$(RUN) -e REPORT_NAME=after-compaction runner verify
else ifeq ($(ARCH),ch-archive)
	$(RUN) ch-workload archive
	$(RUN) -e ROWS=$$(( $(ROWS) / 2 )) runner verify
	$(MAKE) compact
	$(RUN) -e ROWS=$$(( $(ROWS) / 2 )) -e REPORT_NAME=after-compaction runner verify
else ifeq ($(ARCH),duck-archive)
	$(RUN) runner duck-archive
	$(MAKE) compact
	$(RUN) -e ROWS=$$(( $(ROWS) / 2 )) -e REPORT_NAME=after-compaction runner verify
else ifeq ($(ARCH),ch-sharded)
	$(MAKE) cluster-test cluster-failure-test
else
	@echo 'invalid ARCH'; exit 2
endif

probe:
	$(RUN) runner probe

load:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) runner load

verify:
	$(RUN) runner verify

compact:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) compactor

config:
	python3 scripts/cluster-config.py

cluster-up: config
	$(COMPOSE) --profile cluster up -d --wait

cluster-test: load
	$(MAKE) compact
	$(MAKE) cluster-up
	$(RUN) runner verify
	$(RUN) runner native

cluster-failure-test: config
	$(COMPOSE) stop ch21
	$(RUN) runner append-source
	$(RUN) -e ROWS=$$(( $(ROWS) + $(BATCH_ROWS) )) -e SKIP_NODE=ch21 runner native-stage
	$(COMPOSE) up -d --wait ch21
	$(RUN) runner native-recover

status:
	$(COMPOSE) ps --all

cli:
	$(COMPOSE) exec clickhouse clickhouse-client --password local-reader

down:
	$(COMPOSE) --profile cluster rm -sf clickhouse ch11 ch12 ch13 ch21 ch22 ch23 ch31 ch32 ch33

docker-down:
	$(COMPOSE) --profile cluster --profile tools down --volumes --remove-orphans
	@for image in iceberg-multi-runner:dev iceberg-multi-compactor:dev iceberg-multi-ch-workload:dev; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image" || exit $$?; fi; done

.PHONY: benchmark benchmark-case
benchmark: config
	sh scripts/test.sh "$(ARCH)" benchmark-case
benchmark-case: config
ifeq ($(ARCH),ch-sharded)
	$(MAKE) cluster-test
	$(RUN) -e BENCH_NATIVE=true runner benchmark
else
	$(MAKE) test-case ARCH="$(ARCH)"
	$(RUN) -e ROWS=$$(( $(ROWS) $(if $(filter ch-archive duck-archive,$(ARCH)),/ 2,+ 0) )) runner benchmark
endif
