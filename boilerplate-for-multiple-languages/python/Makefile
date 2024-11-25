# note: call scripts from /scripts
SHELL := /bin/bash  # use this instead of /bin/bash

COMMIT := $(shell /usr/bin/git describe --always)
DATE := $(shell /bin/date -u +"%Y-%m-%d-%H:%M")
DISTRO := $(shell lsb_release -cs)
MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
MKFILE_DIR := $(dir $(MKFILE_PATH))

# programs created by this Makefile

# Internal variables

# Ignore distribution check because the makefile contains `make lint` and `make test` as well

# Version check
ifdef CI_COMMIT_TAG
CI_COMMIT_TAG := $(CI_COMMIT_TAG)
else
CI_COMMIT_TAG := 0.0.1rc1
endif

# python client setup
SYSTEM_PYTHON := /usr/bin/python3
PY_CLIENT_BASE_DIR := .
PY_VENV_DIR := ./.venv-client
PY_PIP := ${PY_VENV_DIR}/bin/pip3
PY_TEST := ${PY_VENV_DIR}/bin/pytest
#PY_OAPI_GEN_FILES_DIR := ${PY_CLIENT_BASE_DIR}/app_client
#PY_OAPI_CLIENT := ${PY_VENV_DIR}/bin/openapi-python-client
#PY_RUFF_CLIENT := ${PY_VENV_DIR}/bin/ruff
#PY_OAPI_CLIENT_CONFIG := ${PY_CLIENT_BASE_DIR}/openapi-python-client.config.yaml

.PHONY: all test clean tox py-env py-requirements py-test-requirements
all: clean format test

clean:
	-rm -rf .tox

test:
	${PY_TEST} tests

tox:
	tox --recreate

# setup a virtualenv
py-env:
	${SYSTEM_PYTHON} -m venv ${PY_VENV_DIR}

py-requirements:
	${PY_PIP} install -r ${PY_CLIENT_BASE_DIR}/requirements.txt

py-test-requirements:
	${PY_PIP} install -r ${PY_CLIENT_BASE_DIR}/test_requirements.txt

#py-generate: py-requirements
#	. ${PY_VENV_DIR}/bin/activate
# generate code
#	${PY_OAPI_CLIENT} generate --path ${OAPI_FILE_PATH} --output-path ${PY_OAPI_GEN_FILES_DIR} --config ${PY_OAPI_CLIENT_CONFIG} --overwrite
# create an __init__.py file
#	touch ${PY_OAPI_GEN_FILES_DIR}/__init__.py
# use ruff to format it
#	${PY_RUFF_CLIENT} check ${PY_OAPI_GEN_FILES_DIR} --fix
#	${PY_RUFF_CLIENT} format ${PY_OAPI_GEN_FILES_DIR}
