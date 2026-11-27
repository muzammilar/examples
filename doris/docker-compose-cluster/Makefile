SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name doris-replicated-orders
REVISION ?= 1
.NOTPARALLEL:
.PHONY: docker-up up test verify verify-replicas mutate backend-restart backend-recovery topology status cli report down docker-down
docker-up:
	$(COMPOSE) build fe1 be1 orders
	$(COMPOSE) up -d --wait fe1 fe2 fe3 be1 be2 be3
up:
	mkdir -p results
	$(COMPOSE) run --rm orders setup
	$(MAKE) status > results/initial-topology.txt
test:
	$(COMPOSE) run --rm orders seed
	$(MAKE) verify
	$(MAKE) verify-replicas
	$(MAKE) topology
verify-replicas:
	@for replica in 0 1 2; do $(COMPOSE) run --rm -e REPLICA_INDEX=$$replica -e EXPECTED_REVISION=$(REVISION) orders verify > results/replica-$$replica-revision-$(REVISION).json; done
verify:
	$(COMPOSE) run --rm orders verify > results/correctness.json
mutate:
	$(COMPOSE) run --rm orders mutate
	$(COMPOSE) run --rm -e EXPECTED_REVISION=2 orders verify > results/revision2.json
	$(MAKE) verify-replicas REVISION=2
	$(MAKE) topology
backend-recovery:
	python3 scripts/backend_recovery.py
backend-restart:
	bash scripts/backend_restart.sh
topology:
	python3 scripts/topology.py > results/topology-verdict.json
status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T fe1 mysql -h127.0.0.1 -P9030 -uroot -e 'SHOW FRONTENDS; SHOW BACKENDS; SHOW TABLETS FROM lab.orders;'
report:
	python3 scripts/report.py
cli:
	$(COMPOSE) exec fe1 mysql -h127.0.0.1 -P9030 -uroot
down:
	$(COMPOSE) run --rm orders drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local
