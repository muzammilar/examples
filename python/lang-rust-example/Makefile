# note: call scripts from /scripts
SHELL := /bin/bash  # use this instead of /bin/bash

.PHONY: all sync default

default:
	uv run main.py

all: clean empty-module build-tools default

empty-module:
	mkdir -p my_module
	touch my_module/__init__.py

build-tools:
	uv sync
	uv tool install maturin

# cleans up everything including database and venv
clean:
	-rm -rf .venv
	-rm -rf .tox
	-rm -rf __pycache__
	-rm -rf target
	-rm -f my_module/*.so
