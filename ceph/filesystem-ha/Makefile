SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name ceph-filesystem-ha
export COMPOSE_PROJECT_NAME := ceph-filesystem-ha
PYTHON ?= python3
KERNEL_CLIENT ?= 0
.NOTPARALLEL:
.PHONY: build-guests docker-up status
build-guests:
	@test "$$(docker info --format '{{.Architecture}}')" = aarch64 || { echo "ARM64 Docker engine required"; exit 1; }
	$(PYTHON) scripts/prepare_build.py
	$(COMPOSE) build guests
docker-up: build-guests
	$(PYTHON) scripts/preflight.py
	mkdir -p results .build/files-workload
	$(COMPOSE) up -d guests
	$(COMPOSE) run --rm control /opt/lab/native/guest.py ready
status:
	$(COMPOSE) ps
	$(COMPOSE) run --rm control /opt/lab/native/guest.py ssh 0 'ceph -s'

.PHONY: build-workload up
build-workload:
	docker build -f files-workload/Dockerfile --target artifact --output type=local,dest=.build/files-workload .
up: build-workload
	$(PYTHON) scripts/guard.py
	$(COMPOSE) run --rm control /opt/lab/native/bootstrap.py
	$(COMPOSE) run --rm control /opt/lab/native/workload.py install
	@if [ "$(KERNEL_CLIENT)" = 1 ]; then $(MAKE) kernel-up; fi

FILES ?= 128
FAILURE_PAYLOAD_BYTES ?= 65536
export FAILURE_PAYLOAD_BYTES
PAYLOAD_BYTES ?= 262144
export FILES PAYLOAD_BYTES
.PHONY: test verify
test:
	$(PYTHON) scripts/test.py baseline
	@if [ "$(KERNEL_CLIENT)" = 1 ]; then $(MAKE) kernel-test; fi
verify:
	$(PYTHON) scripts/test.py verify

.PHONY: failover test-production
failover:
	$(PYTHON) scripts/failover.py
test-production:
	$(MAKE) verify
	$(MAKE) failover
	$(MAKE) guest-failure
	$(MAKE) disk-failure
	@if [ "$(KERNEL_CLIENT)" = 1 ]; then $(MAKE) kernel-test-production; fi
	$(MAKE) verify

.PHONY: resources down docker-down
resources:
	$(PYTHON) scripts/resources.py
down:
	$(PYTHON) scripts/cleanup.py down
docker-down:
	$(PYTHON) scripts/cleanup.py docker-down

.PHONY: unit-test
unit-test:
	$(PYTHON) -m unittest discover -s tests -v

.PHONY: recover-keyrings
recover-keyrings:
	$(PYTHON) scripts/recover_keyrings.py

.PHONY: cli
cli:
	$(COMPOSE) exec guests ssh -F /dev/null -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -i /state/id_ed25519 -p 2221 -t root@127.0.0.1

.PHONY: build-kernel kernel-up kernel-test kernel-verify
build-kernel:
	$(PYTHON) projects/kernel-client/prepare.py
	docker build -f projects/kernel-client/Dockerfile --target artifact --output type=local,dest=.build/kernel-workload-artifact .
kernel-up: build-kernel
	$(PYTHON) projects/kernel-client/control.py up
kernel-test:
	$(PYTHON) projects/kernel-client/control.py test
kernel-verify:
	$(PYTHON) projects/kernel-client/control.py verify

.PHONY: kernel-compare
kernel-compare:
	$(PYTHON) projects/kernel-client/compare.py

.PHONY: kernel-reopen
kernel-reopen:
	$(PYTHON) projects/kernel-client/control.py reopen

.PHONY: kernel-mds-failure
kernel-mds-failure:
	$(PYTHON) projects/kernel-client/mds_failure.py

.PHONY: kernel-test-production
kernel-test-production:
	$(MAKE) kernel-verify
	$(MAKE) kernel-mds-failure
	$(MAKE) kernel-verify

.PHONY: guest-failure disk-failure
guest-failure:
	$(PYTHON) scripts/guest_failure.py process
disk-failure:
	$(PYTHON) scripts/guest_failure.py disk

.PHONY: backup restore
backup:
	$(PYTHON) scripts/backup.py create
restore:
	@test -n "$(BACKUP_MANIFEST)" || { echo 'Set BACKUP_MANIFEST=results/backups/<id>/manifest.json'; exit 1; }
	BACKUP_MANIFEST="$(BACKUP_MANIFEST)" $(PYTHON) scripts/backup.py restore
