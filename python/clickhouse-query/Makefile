# note: call scripts from /scripts
SHELL := /bin/bash  # use this instead of /bin/bash

COMMIT := $(shell /usr/bin/git describe --always)
DATE := $(shell /bin/date -u +"%Y-%m-%d-%H:%M")
DISTRO := $(shell lsb_release -cs)
MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
MKFILE_DIR := $(dir $(MKFILE_PATH))

# Development only variables
DEV_VENV_PATH := ~/testvenv

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
	-rm -f dist/*.deb
	-rm -rf debian/${PACKAGE_NAME}
	-rm -rf debian/.debhelper/
	rm -fv dist/*

test:
	tox --recreate

dev-setup:
# setup a virtualenv
	python3 -m venv ${DEV_VENV_PATH}
	${DEV_VENV_PATH}/bin/pip3 install -r ${MKFILE_DIR}/requirements.txt
	${DEV_VENV_PATH}/bin/pip3 install -r ${MKFILE_DIR}/test_requirements.txt

wheel: wheel_build wheel_inspect

wheel_build:
	python3 setup.py -q bdist_wheel
