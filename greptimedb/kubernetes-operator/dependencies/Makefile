SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= greptime-kind-support
RESULTS_DIR := $(CURDIR)/results
GARAGE_CONFIG_DIR := $(CURDIR)/config/generated
S3_ACCESS_KEY := GK0123456789abcdef01234567
S3_SECRET_KEY := local-only-object-storage-secret-key
SERIES ?= 10000
POINTS ?= 100
LOGS ?= 1000000
export COMPOSE_PROJECT_NAME RESULTS_DIR GARAGE_CONFIG_DIR S3_ACCESS_KEY S3_SECRET_KEY SERIES POINTS LOGS
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm client
.NOTPARALLEL:
.PHONY: config dependencies docker-down
config:
	python3 scripts/garage_config.py
	mkdir -p results
dependencies: config
	$(COMPOSE) build client vlogs
	$(COMPOSE) up -d --wait garage0 garage1 garage2 garage3 garage4 garage5 garage6 garage7 garage8 s3 meta-store0 meta-store1 meta-store2 wal0 wal1 wal2
	python3 scripts/garage_bootstrap.py
	$(COMPOSE) exec -T wal0 rpk cluster config set write_caching_default false
	$(COMPOSE) exec -T wal0 rpk cluster config set default_topic_replications 3
	$(COMPOSE) exec -T wal0 rpk cluster config set default_topic_partitions 1
	$(COMPOSE) up -d --wait vmstore0 vmstore1 vmstore2 vminsert vmselect vlogs
docker-down:
	$(COMPOSE) --profile tools --profile scale down --volumes --remove-orphans
	@for image in '$(COMPOSE_PROJECT_NAME)-client:dev' '$(COMPOSE_PROJECT_NAME)-tools:dev' '$(COMPOSE_PROJECT_NAME)-vlogs:dev'; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
	rm -rf config/generated
