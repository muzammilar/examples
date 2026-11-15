SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= clickhouse-s3-tiered-storage
RESULTS_DIR ?= $(CURDIR)/results/verified
GARAGE_CONFIG_DIR := $(CURDIR)/config/generated
S3_ACCESS_KEY := GK0123456789abcdef01234567
S3_SECRET_KEY := local-only-object-storage-secret-key
ROWS ?= 2000000
QUERY_RUNS ?= 20
export COMPOSE_PROJECT_NAME RESULTS_DIR GARAGE_CONFIG_DIR S3_ACCESS_KEY S3_SECRET_KEY ROWS QUERY_RUNS
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm --user $(shell id -u):$(shell id -g) runner
BACKEND := garage0 garage1 garage2 garage3 garage4 garage5 garage6 garage7 garage8 s3
.NOTPARALLEL:
.PHONY: config docker-up up test benchmark failover disk-failure status cli down docker-down
config:
	python3 ../../garage/s3-cluster/scripts/config.py
	python3 scripts/config.py
	mkdir -p $(RESULTS_DIR)
docker-up: config
	$(COMPOSE) up -d --wait $(BACKEND) keeper0 keeper1 keeper2
	python3 ../../garage/s3-cluster/scripts/bootstrap.py
up:
	$(COMPOSE) build runner
	$(COMPOSE) up -d --wait ch0 ch1 ch2
	$(RUN) seed
test:
	$(RUN) test
benchmark:
	$(RUN) benchmark
failover:
	python3 scripts/fault.py process
disk-failure:
	python3 scripts/fault.py disk
status:
	$(COMPOSE) ps
cli:
	$(COMPOSE) exec ch0 clickhouse-client --password local-only-clickhouse
down:
	$(RUN) drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans
	@for image in '$(COMPOSE_PROJECT_NAME)-runner:dev' '$(COMPOSE_PROJECT_NAME)-tools:dev'; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
	rm -rf config/generated

.PHONY: backend-failure
backend-failure:
	python3 scripts/fault.py backend
