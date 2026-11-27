SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name doris-single-node
.NOTPARALLEL:
.PHONY: docker-up up test verify down docker-down status
docker-up:
	$(COMPOSE) build fe be orders
	$(COMPOSE) up -d --wait fe be
up:
	$(COMPOSE) run --rm orders setup
test:
	mkdir -p results
	$(COMPOSE) run --rm orders seed
	$(MAKE) verify
verify:
	$(COMPOSE) run --rm orders verify > results/correctness.json
status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T fe mysql -h127.0.0.1 -P9030 -uroot -e 'SHOW FRONTENDS; SHOW BACKENDS;'
down:
	$(COMPOSE) run --rm orders drop
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local

.PHONY: backend-restart
backend-restart:
	mkdir -p results
	$(COMPOSE) run --rm orders verify > results/before-crash.json
	$(COMPOSE) kill --signal SIGKILL be
	docker inspect doris-single-node-be-1 --format '{{json .State}}' > results/killed-backend.json
	$(COMPOSE) up --no-deps -d --wait be
	$(MAKE) up
	$(COMPOSE) run --rm orders verify > results/after-crash.json
