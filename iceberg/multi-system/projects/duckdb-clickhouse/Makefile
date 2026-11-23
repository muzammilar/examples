# Project entry points; infrastructure belongs to the enclosing lab.
LAB_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
.DEFAULT_GOAL := help
.NOTPARALLEL:
.PHONY: help docker-up up test down docker-down check-controls
help:
	@echo "DuckDB → ClickHouse ↔ Iceberg: make docker-up up test down docker-down (separate invocations)"
docker-up:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT=http docker-up
up:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT=http up
test:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT=http duckdb-clickhouse
down:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT=http down
docker-down:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT=http docker-down
check-controls:
	$(MAKE) -C "$(LAB_DIR)" LAB_VARIANT=http recovery-unit-test
