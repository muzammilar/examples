SHELL := /bin/sh
COMPOSE := docker compose
QUERY_RUNS ?= 20
export QUERY_RUNS
ROWS ?= 2000000
CUSTOMERS ?= 10000
BATCH_ROWS ?= 100000
TARGET_FILE_BYTES ?= 134217728
RESULTS_DIR ?= results/manual
export ROWS CUSTOMERS BATCH_ROWS TARGET_FILE_BYTES RESULTS_DIR
LOCAL_UID := $(shell id -u)
export LOCAL_UID
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g)
.NOTPARALLEL:
.PHONY: unit-test docker-up up test direct-test direct-case archive-test archive-case compact verify restart-test failure-test status cli down docker-down

docker-up:
	@docker info >/dev/null
	$(COMPOSE) up -d --wait storage

up:
	$(COMPOSE) build runner compactor
	$(COMPOSE) up -d --wait storage clickhouse
	@mkdir -p "$(RESULTS_DIR)"

unit-test:
	docker run --rm --network none --cpus=2 --memory=1g --user $(shell id -u):$(shell id -g) -e GOCACHE=/tmp/go-cache -e GOMODCACHE=/tmp/go-mod-cache -v "$(CURDIR)/app:/src:ro" -w /src golang:1.27.4-bookworm go test ./...

test: unit-test direct-test archive-test

direct-test:
	@mkdir -p "$(RESULTS_DIR)/direct"
	$(MAKE) direct-case RESULTS_DIR="$(RESULTS_DIR)/direct"

direct-case:
	$(RUN) runner direct
	$(MAKE) compact
	$(RUN) runner verify
	$(RUN) runner append
	$(RUN) runner verify-snapshot
	$(MAKE) restart-test

archive-test:
	@mkdir -p "$(RESULTS_DIR)/archive"
	$(MAKE) archive-case RESULTS_DIR="$(RESULTS_DIR)/archive"

archive-case:
	$(RUN) runner archive
	$(MAKE) compact
	$(RUN) runner verify-archive

compact:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) compactor

verify:
	$(RUN) runner verify-archive

restart-test:
	$(COMPOSE) restart storage clickhouse
	$(COMPOSE) up -d --wait storage clickhouse
	$(RUN) -e ROWS=$$(( $(ROWS) + $(BATCH_ROWS) )) runner verify
	$(RUN) runner verify-snapshot

failure-test:
	@mkdir -p "$(RESULTS_DIR)"
	@if $(RUN) -e FAIL_AT=before-export runner archive; then echo 'injection did not fail'; exit 1; else code=$$?; test $$code -eq 42 || exit $$code; fi
	$(RUN) runner assert-retained
	$(RUN) runner resume-archive
	@if $(RUN) -e FAIL_AT=after-export runner archive; then echo 'injection did not fail'; exit 1; else code=$$?; test $$code -eq 42 || exit $$code; fi
	$(RUN) runner assert-retained
	$(COMPOSE) restart storage clickhouse
	$(COMPOSE) up -d --wait storage clickhouse
	$(RUN) runner resume-archive
	@if $(RUN) -e FAIL_AT=verification runner archive; then echo 'injection did not fail'; exit 1; else code=$$?; test $$code -eq 42 || exit $$code; fi
	$(RUN) runner assert-retained
	$(RUN) runner resume-archive

status:
	$(COMPOSE) ps --all

cli:
	$(COMPOSE) exec clickhouse clickhouse-client --password local-reader

down:
	$(COMPOSE) rm -sf clickhouse

docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@for image in clickhouse-iceberg-runner:dev clickhouse-iceberg-compactor:dev; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image" || exit $$?; fi; done

.PHONY: benchmark benchmark-direct benchmark-archive benchmark-direct-case benchmark-archive-case
benchmark: benchmark-direct benchmark-archive
benchmark-direct:
	@mkdir -p "$(RESULTS_DIR)/benchmark-direct"
	$(MAKE) benchmark-direct-case RESULTS_DIR="$(RESULTS_DIR)/benchmark-direct"
benchmark-direct-case:
	$(RUN) runner direct
	$(MAKE) compact
	$(RUN) runner benchmark
benchmark-archive:
	@mkdir -p "$(RESULTS_DIR)/benchmark-archive"
	$(MAKE) benchmark-archive-case RESULTS_DIR="$(RESULTS_DIR)/benchmark-archive"
benchmark-archive-case:
	$(RUN) runner archive
	$(MAKE) compact
	$(RUN) -e BENCH_MODE=archive runner benchmark
