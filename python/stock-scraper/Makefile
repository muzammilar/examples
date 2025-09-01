# note: call scripts from /scripts
SHELL := /bin/bash  # use this instead of /bin/bash

PROG_ROOT := webapp
PROG_NAME := main.py

.PHONY: all sync format purge
all: sync format-check

# cleans up everything including database and venv
purge: cleandb
	-rm -rf .venv
	-rm -rf .tox
	-rm -rf __pycache__

# format the code
format:
#	uv tool run ruff format .
	uv format -- --line-length 120

format-check:
	uv format --diff
	uv format --check

# sync the dependencies including venv
sync:
	uv sync
