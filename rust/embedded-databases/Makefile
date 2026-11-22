SHELL := /bin/sh
LOCAL_UID := $(shell id -u)
LOCAL_GID := $(shell id -g)
SEED_PAIRS ?= 100000
export LOCAL_UID LOCAL_GID SEED_PAIRS
.NOTPARALLEL:
.PHONY: docker-up up test test-production crash-test status cli down docker-down report check-controls

docker-up up test test-production crash-test status cli down docker-down report:
	python3 run.py $@

check-controls:
	python3 -m unittest test_run.py
