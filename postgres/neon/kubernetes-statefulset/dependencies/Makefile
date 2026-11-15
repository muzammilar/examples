SHELL := /bin/sh
COMPOSE_PROJECT_NAME ?= neon-kind-support
ROWS ?= 1000000
FAULT_SERVICE ?= sk0
RESULTS_DIR := $(CURDIR)/results
GARAGE_CONFIG_DIR := $(CURDIR)/config/generated
S3_ACCESS_KEY := GK0123456789abcdef01234567
S3_SECRET_KEY := local-only-object-storage-secret-key
export COMPOSE_PROJECT_NAME ROWS FAULT_SERVICE RESULTS_DIR GARAGE_CONFIG_DIR S3_ACCESS_KEY S3_SECRET_KEY
COMPOSE := docker compose
RUN := $(COMPOSE) run --rm runner
.NOTPARALLEL:
.PHONY: dependencies docker-down
dependencies:
	python3 scripts/garage_config.py
	mkdir -p results/generated
	$(COMPOSE) build runner
	$(COMPOSE) up -d --wait garage0 garage1 garage2 garage3 garage4 garage5 garage6 garage7 garage8 s3
	python3 scripts/garage_bootstrap.py
	$(COMPOSE) up -d --wait pg0 pg1 pg2
	$(COMPOSE) exec -T pg0 psql -U postgres -v ON_ERROR_STOP=1 -c "ALTER SYSTEM SET synchronous_standby_names = 'ANY 1 (pg1,pg2)'"
	$(COMPOSE) exec -T pg0 psql -U postgres -v ON_ERROR_STOP=1 -c 'SELECT pg_reload_conf()'
	$(RUN) wait-baseline
docker-down:
	$(COMPOSE) --profile tools --profile workload down --volumes --remove-orphans
	@for image in '$(COMPOSE_PROJECT_NAME)-runner:dev' '$(COMPOSE_PROJECT_NAME)-tools:dev'; do if docker image inspect "$$image" >/dev/null 2>&1; then docker image rm "$$image"; fi; done
	rm -rf config/generated
