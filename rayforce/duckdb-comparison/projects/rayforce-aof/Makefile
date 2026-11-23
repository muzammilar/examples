# Project entry points; infrastructure belongs to the enclosing lab.
LAB_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
.DEFAULT_GOAL := help
.NOTPARALLEL:
.PHONY: help docker-up up test down docker-down check-controls
help:
	@echo "Rayforce AOF integrity: make docker-up up test down docker-down (separate invocations)"
docker-up:
	$(MAKE) -C "$(LAB_DIR)" docker-up
up:
	$(MAKE) -C "$(LAB_DIR)" up
test:
	$(MAKE) -C "$(LAB_DIR)" test-aof-integrity
down:
	$(MAKE) -C "$(LAB_DIR)" down
docker-down:
	$(MAKE) -C "$(LAB_DIR)" docker-down
check-controls:
	$(MAKE) -C "$(LAB_DIR)" check-controls
