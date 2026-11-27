SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name hadoop-metadata-ha
.NOTPARALLEL:
.PHONY: build docker-up up test verify failover test-production fence-controls status cli down docker-down
build:
	mkdir -p results
	docker build -f docker/Dockerfile.hadoop -t examples/hadoop-metadata-ha:3.5.0 .
	docker build -f fencing/openssh/Dockerfile -t examples/hadoop-ha-openssh:3.5.0 .
	docker build -f files-workload/Dockerfile -t examples/hadoop-ha-files:3.5.0 .
docker-up: build
	$(COMPOSE) run --rm keys
	$(COMPOSE) up -d --wait zk1 zk2 zk3 jn1 jn2 jn3
up:
	python3 scripts/bootstrap.py
test:
	python3 scripts/test.py baseline
verify:
	python3 scripts/test.py verify
failover:
	python3 scripts/test.py failover
fence-controls:
	python3 scripts/fence_controls.py
test-production:
	$(MAKE) verify
	$(MAKE) failover
	$(MAKE) verify
status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T --user hadoop nn1 hdfs haadmin -getAllServiceState
cli:
	$(COMPOSE) run --rm --user hadoop --entrypoint bash tools
down:
	python3 scripts/teardown.py workload
docker-down:
	python3 scripts/teardown.py infrastructure
