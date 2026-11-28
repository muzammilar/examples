SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
FILES ?= 128
LIVE_FILES ?= 512
PAYLOAD_BYTES ?= 262144
export FILES LIVE_FILES PAYLOAD_BYTES
.NOTPARALLEL:
.PHONY: prepare check docker-up up test verify backup locks metadata-outage object-store-outage test-production down docker-down report status
prepare:
	$(PYTHON) scripts/prepare.py
check: prepare
	$(PYTHON) -m unittest discover -s tests
	bash -n file-publication/mount.sh
	docker compose --profile tools config --quiet
docker-up: prepare
	$(PYTHON) scripts/observe.py docker-up
up test verify backup locks metadata-outage object-store-outage down docker-down:
	$(PYTHON) scripts/observe.py $@
test-production:
	$(MAKE) locks
	$(MAKE) metadata-outage
	$(MAKE) object-store-outage
	$(MAKE) verify
report:
	$(PYTHON) scripts/report.py
status:
	docker compose ps --all
