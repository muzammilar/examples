SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
ORDER_COUNT ?= 1000000
CUSTOMER_COUNT ?= 10000
INSERT_RATE ?= 10000
export ORDER_COUNT CUSTOMER_COUNT INSERT_RATE
.NOTPARALLEL:
.PHONY: check docker-up up test verify publication-failure compaction-failure test-production report status down docker-down
check:
	$(PYTHON) -m unittest discover -s tests
	$(PYTHON) scripts/render_sql.py
	docker compose --profile tools config --quiet
docker-up up test verify publication-failure compaction-failure down docker-down:
	$(PYTHON) scripts/lifecycle.py $@
test-production:
	$(MAKE) publication-failure
	$(MAKE) compaction-failure
	$(MAKE) verify
status:
	docker compose ps --all
report:
	$(PYTHON) scripts/curate.py
