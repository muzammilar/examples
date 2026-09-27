# note: call scripts from /scripts
SHELL := /bin/bash  # use this instead of /bin/bash

COMMIT := $(shell /usr/bin/git describe --always)
DATE := $(shell /bin/date -u +"%Y-%m-%d-%H:%M")
DISTRO := $(shell lsb_release -cs)
MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
MKFILE_DIR := $(dir $(MKFILE_PATH))


PY_TEST:= pytest
# programs created by this Makefile

# Internal variables

# Ignore distribution check because the makefile contains `make lint` and `make test` as well

# Version check
ifdef CI_COMMIT_TAG
CI_COMMIT_TAG := $(CI_COMMIT_TAG)
else
CI_COMMIT_TAG := 0.0.1rc1
endif

.PHONY: all test clean format lint deb install uninstall dev-setup dev-activate
all: clean format test

clean:
	-rm -rf .tox

test:
	${PY_TEST} tests
