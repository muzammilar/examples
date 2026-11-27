SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name flink-state-recovery
.NOTPARALLEL:
.PHONY: docker-up up test verify status down docker-down
docker-up:
	$(COMPOSE) build jobmanager verifier
	$(COMPOSE) up -d --wait jobmanager taskmanager
up:
	mkdir -p results
	$(COMPOSE) run --rm --entrypoint python verifier /app/wait_workers.py > results/topology.json
	$(COMPOSE) exec -T --user root jobmanager bash -c 'mkdir -p /data/orders && chown -R flink:flink /data'
test:
	mkdir -p results
	$(COMPOSE) run --rm sql > results/sql.log 2>&1
	$(MAKE) verify
verify:
	$(COMPOSE) run --rm -T verifier > results/correctness.json
status:
	$(COMPOSE) ps
down:
	$(COMPOSE) exec -T jobmanager bash -c 'find /data/orders -type f -delete'
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local

.PHONY: task-recovery
task-recovery:
	python3 scripts/task_recovery.py
