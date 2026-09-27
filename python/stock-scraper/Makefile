# note: call scripts from /scripts
SHELL := /bin/bash  # use this instead of /bin/bash

PROG_NAME := main.py

.PHONY: all sync format format-check lint login run purge
all: sync format-check lint

# remove the virtualenv and caches (keeps browser profiles and scraped data)
purge:
	-rm -rf .venv
	-rm -rf .ruff_cache
	-find . -name __pycache__ -type d -prune -exec rm -rf {} +

# format the code (settings come from ruff.toml)
format:
	uv format

format-check:
	uv format --check --diff

lint:
	uvx ruff check .

# sync the dependencies including venv
sync:
	uv sync

# open the browser and log in manually; the session is stored in the browser profile
login: sync
	uv run $(PROG_NAME) --initial-login

# scrape using the saved session
run: sync
	uv run $(PROG_NAME)
