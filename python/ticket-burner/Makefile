# note: call scripts from /scripts
SHELL := /bin/bash  # use this instead of /bin/bash

PROG_ROOT := webapp
PROG_NAME := main.py
DB_FILE := example.db

.PHONY: all dev sync run
all: cleandb dev

# cleans up the database files
cleandb:
	-rm -rf ${PROG_ROOT}/${DB_FILE}

# cleans up everything including database and venv
purge: cleandb
	-rm -rf .venv
	-rm -rf .tox
	-rm -rf __pycache__

# sync the dependencies including venv
sync:
	uv sync

# Run the main asynchronous function in debug mode
dev: cleandb sync
	cd ${PROG_ROOT} && uv run fastapi --verbose dev --host 0.0.0.0


run: cleandb sync
	uv run fastapi ${PROG_ROOT}/${PROG_NAME}
