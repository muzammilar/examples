# This route uses the parent multi-system lab infrastructure.
LAB_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../../..)
.DEFAULT_GOAL := help
.NOTPARALLEL:
.PHONY: help docker-up up test down docker-down check-controls
help:
	@echo "clickhouse-scanner: make docker-up, up, test, down, docker-down (separate commands)"
docker-up:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT= docker-up
up:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT= up
test:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT= duckdb-clickhouse-native
down:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT= down
docker-down:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT= docker-down
check-controls:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT= recovery-unit-test
.PHONY: read-repro read-isolation
read-repro:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT= duckdb-clickhouse-read-repro
read-isolation:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT= connector-read-isolation
