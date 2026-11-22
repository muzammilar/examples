SHELL := /bin/sh
DUCKDB_VERSION ?= 2.0.1
export DUCKDB_VERSION
ifneq ($(words $(DUCKDB_VERSION)),1)
$(error Select exactly one DUCKDB_VERSION)
endif
ifeq ($(filter 1.5.5 2.0.1,$(DUCKDB_VERSION)),)
$(error DUCKDB_VERSION must be 2.0.1 or the historical 1.5.5 lane)
endif
LAB_PROFILE ?= current
export LAB_PROFILE
ifneq ($(words $(LAB_PROFILE)),1)
$(error Select exactly one LAB_PROFILE)
endif
ifeq ($(filter historical current,$(LAB_PROFILE)),)
$(error LAB_PROFILE must be historical or current)
endif
LAB_PROJECT := iceberg-multi-system$(if $(filter current,$(LAB_PROFILE)),-current,)
COMPOSE := docker compose --project-name $(LAB_PROJECT) --env-file /dev/null -f docker-compose.yml -f config/generated/cluster.yml -f docker-compose.recovery.yml
ifeq ($(DUCKDB_VERSION),2.0.1)
COMPOSE += -f docker-compose.duckdb2.yml
endif
ifeq ($(LAB_PROFILE),current)
ifneq ($(DUCKDB_VERSION),2.0.1)
$(error The current lab requires DuckDB 2.0.1)
endif
COMPOSE += -f docker-compose.storage-current.yml -f docker-compose.maintenance-current.yml -f docker-compose.current.yml
RESULTS_DIR ?= results/current/manual
endif
QUERY_RUNS ?= 20
export QUERY_RUNS
ROWS ?= 2000000
CUSTOMERS ?= 10000
BATCH_ROWS ?= 100000
TARGET_FILE_BYTES ?= 134217728
TABLE ?= lab.events
RESULTS_DIR ?= results/manual
export ROWS CUSTOMERS BATCH_ROWS TARGET_FILE_BYTES TABLE RESULTS_DIR
RECOVERY_ROWS ?= 2000000
RECOVERY_BATCH_ROWS ?= 10000
RECOVERY_WRITERS ?= 2
RECOVERY_TIMEOUT_S ?= 10
RECOVERY_DELAY_MS ?= 1000
RECOVERY_VISIBILITY_S ?= 30
RECOVERY_VERIFY_TIMEOUT_S ?= 300
export RECOVERY_ROWS RECOVERY_BATCH_ROWS RECOVERY_WRITERS RECOVERY_TIMEOUT_S
export RECOVERY_DELAY_MS RECOVERY_VISIBILITY_S RECOVERY_VERIFY_TIMEOUT_S
LOCAL_UID := $(shell id -u)
export LOCAL_UID
LOCAL_USER := $(shell id -u):$(shell id -g)
CLUSTER_DATA_CPUS ?= 0.5
export CLUSTER_DATA_CPUS
CLUSTER_NODES := $(foreach shard,1 2 3,$(foreach replica,1 2 3,ch$(shard)$(replica))) keeper1 keeper2 keeper3
RUN := $(COMPOSE) run --rm --user $(LOCAL_USER)
.NOTPARALLEL:

.PHONY: prepare unit-test config test-case cluster-up cluster-down cluster-test cluster-failure-test
.PHONY: docker-up up test probe load verify compact status cli down docker-down
.PHONY: recovery-unit-test storage-crash compactor-crash manifest-io verify-history retire-failed-case test-production

docker-up: config
	@docker info >/dev/null
	$(COMPOSE) up -d --wait storage

prepare:
	python3 prepare.py

up: config prepare
	$(COMPOSE) build runner compactor ch-workload compaction-upload-gate
	$(COMPOSE) up -d --wait storage clickhouse
	@mkdir -p "$(RESULTS_DIR)"

ARCH ?= all
export ARCH

unit-test: config prepare
	$(MAKE) recovery-unit-test
	$(COMPOSE) build runner
	docker run --rm --network none --cpus=2 --memory=1g --memory-swap=1g --user $(LOCAL_USER) -e GOMAXPROCS=2 -e GOCACHE=/tmp/go-cache -e GOMODCACHE=/tmp/go-mod-cache -v "$(CURDIR)/.build/clickhouse-archive-workload:/src:ro" -w /src golang:1.27.4-bookworm go test ./...

recovery-unit-test:
	python3 -c 'import sys; assert sys.version_info >= (3, 11), "Recovery controls require Python 3.11+; check the python3 selected by Make"'
	LAB_PROFILE=historical python3 -m unittest test_recovery_resources.py test_recovery_control.py test_recovery_report.py test_recovery_cleanup.py test_recovery_retirement.py test_compactor_cleanup.py test_compactor_control.py test_prefault_archive.py test_manifest_control.py test_cold_storage.py test_cold_readback.py test_cold_upgrade.py test_cold_cleanup.py test_compound_archive.py test_maintenance_control.py test_lab_profiles.py test_duckdb_maintenance.py

.PHONY: duckdb-maintenance duckdb-rejections
duckdb-maintenance: config recovery-unit-test ## Native DuckDB v2 compaction, rollback, retained snapshot and no-op checks
	python3 duckdb_maintenance.py

duckdb-rejections: config recovery-unit-test ## Native pending-write/schema and v3 compaction rejection controls
	python3 duckdb_maintenance.py --rejections

storage-crash: config recovery-unit-test
	python3 recovery_control.py storage-crash

compactor-crash: config recovery-unit-test
	python3 compactor_control.py

MANIFEST_CASE ?=
MANIFEST_INSPECTION ?=
MANIFEST_VERIFICATION ?=
manifest-io: config recovery-unit-test
	python3 manifest_control.py --case "$(MANIFEST_CASE)" $(if $(MANIFEST_INSPECTION),--inspection-id "$(MANIFEST_INSPECTION)") $(if $(MANIFEST_VERIFICATION),--verification-id "$(MANIFEST_VERIFICATION)")

verify-history: config recovery-unit-test
	python3 recovery_cleanup.py verify-history

retire-failed-case: config recovery-unit-test
	python3 recovery_cleanup.py retire-failed-case --case "$(FAILED_CASE)" --source-manifest "$(SOURCE_MANIFEST)" --original-manifest "$(ORIGINAL_MANIFEST)" --completed-verification "$(COMPLETED_VERIFICATION)"

test-production:
	$(MAKE) storage-crash
	$(MAKE) verify-history
	$(MAKE) compactor-crash
	$(MAKE) verify-history

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

probe: config
	$(RUN) runner probe

load: config
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) runner load

verify: config
	$(RUN) runner verify

compact: config
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) compactor

config:
	python3 lab_profiles.py
	python3 scripts/cluster-config.py

cluster-up: config
	$(COMPOSE) --profile cluster up -d --wait

cluster-down: config
	$(COMPOSE) --profile cluster stop $(CLUSTER_NODES)

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
	python3 recovery_cleanup.py down

docker-down:
	python3 recovery_cleanup.py docker-down

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

.PHONY: cold-storage
cold-storage: config recovery-unit-test ## Cold checkpoint and byte-identical restore, retaining original state
	python3 cold_storage.py

COLD_CHECKPOINT ?=
COLD_CASE ?=
COLD_VERIFICATION ?=
.PHONY: cold-readback
cold-readback: config recovery-unit-test ## Reconcile one witnessed table on its separate restored volume
	@test -n "$(COLD_CHECKPOINT)" -a -n "$(COLD_CASE)" -a -n "$(COLD_VERIFICATION)" || (echo 'Set COLD_CHECKPOINT, COLD_CASE and COLD_VERIFICATION'; exit 1)
	python3 cold_readback.py --checkpoint "$(COLD_CHECKPOINT)" --case "$(COLD_CASE)" --verification "$(COLD_VERIFICATION)"

COLD_READBACK ?=
.PHONY: cold-readback-cleanup cold-checkpoint-cleanup cold-upgrade
cold-readback-cleanup: config recovery-unit-test ## Remove verified stopped readback containers, network and query cache volume
	@test -n "$(COLD_READBACK)" || (echo 'Set COLD_READBACK'; exit 1)
	python3 cold_cleanup.py --readback "$(COLD_READBACK)"

cold-checkpoint-cleanup: config recovery-unit-test ## Remove recorded stopped checkpoint utilities, retaining all volumes and archives
	@test -n "$(COLD_CHECKPOINT)" || (echo 'Set COLD_CHECKPOINT'; exit 1)
	python3 cold_cleanup.py --checkpoint "$(COLD_CHECKPOINT)"

cold-upgrade: config recovery-unit-test ## Reconcile a restored table with pinned SeaweedFS 4.54; refuse implicit downgrade
	@test -n "$(COLD_CHECKPOINT)" -a -n "$(COLD_CASE)" -a -n "$(COLD_VERIFICATION)" || (echo 'Set COLD_CHECKPOINT, COLD_CASE and COLD_VERIFICATION'; exit 1)
	python3 cold_upgrade.py --checkpoint "$(COLD_CHECKPOINT)" --case "$(COLD_CASE)" --verification "$(COLD_VERIFICATION)"

.PHONY: archive-compound-case
archive-compound-case: config recovery-unit-test ## Preserve the known failed compound case without passing its live test
	@test -n "$(FAILED_CASE)" || (echo 'Set FAILED_CASE to the registered compound-failure case'; exit 1)
	python3 recovery_cleanup.py archive-compound-case --case "$(FAILED_CASE)"

.PHONY: build-current-compactor
build-current-compactor: config prepare ## Build the compatible Spark 4.1.4 / Iceberg 1.12.0 lane without starting services
	$(COMPOSE) -f docker-compose.maintenance-current.yml build compactor

.PHONY: test-current-compactor
test-current-compactor: config recovery-unit-test ## Offline 2M-row compaction, append and retained-snapshot correctness on the current maintenance image
	python3 maintenance_control.py
