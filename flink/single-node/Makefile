SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name flink-local-orders
.NOTPARALLEL:
.PHONY: prepare docker-up up test verify status cli down docker-down
prepare:
	python3 scripts/prepare.py
	git check-ignore .build/sql/orders.sql .build/verify.py .build/source-inventory.json
docker-up: prepare
	$(COMPOSE) build local-sql orders-verifier
	$(COMPOSE) run --rm --user 0 --entrypoint python orders-verifier -c "from pathlib import Path; import os; p=Path('/data'); p.mkdir(exist_ok=True); os.chown(p,9999,9999)"
up:
	mkdir -p results
	$(COMPOSE) run --rm --entrypoint python orders-verifier -c "from pathlib import Path; assert Path('/data').is_dir()"
test:
	$(COMPOSE) run --rm --entrypoint python orders-verifier -c "from pathlib import Path; assert not Path('/data/orders').exists(), 'Output already exists; preserve/verify it before cleanup'"
	$(COMPOSE) run --rm local-sql > results/sql.log 2>&1
	$(MAKE) verify
verify:
	$(COMPOSE) run --rm orders-verifier > results/verification.json
status:
	$(COMPOSE) ps -a
cli:
	$(COMPOSE) run --rm local-sql -Dexecution.target=local
down:
	$(COMPOSE) run --rm --entrypoint python orders-verifier -c "import shutil; from pathlib import Path; p=Path('/data/orders'); shutil.rmtree(p) if p.exists() else None"
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local
