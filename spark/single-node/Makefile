SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name spark-local-orders
.NOTPARALLEL:
.PHONY: prepare docker-up up test verify reader-crash test-production status cli down docker-down
prepare:
	python3 scripts/prepare.py
	git check-ignore .build/orders.py .build/verify.py .build/source-inventory.json
docker-up: prepare
	mkdir -p results
	$(COMPOSE) build orders parquet-verifier
	$(COMPOSE) run --rm --user 0 --entrypoint python3 orders -c "from pathlib import Path; import os; p=Path('/data/events'); p.mkdir(parents=True,exist_ok=True); os.chown('/data',185,0); os.chown(p,185,0)"
up:
	mkdir -p results
	$(COMPOSE) run --rm --entrypoint python3 orders -c "from pathlib import Path; import os; assert Path('/data/events').is_dir() and os.access('/data',os.W_OK)"
test:
	python3 scripts/run.py write
verify:
	python3 scripts/run.py verify
reader-crash:
	python3 scripts/reader_crash.py
	$(MAKE) verify
test-production:
	$(MAKE) test
	$(MAKE) reader-crash
status:
	$(COMPOSE) --profile tools ps -a
cli:
	$(COMPOSE) run --rm --entrypoint /opt/spark/bin/spark-sql orders --master 'local[1]'
down:
	$(COMPOSE) run --rm --user 0 --entrypoint python3 orders -c "import shutil; from pathlib import Path; p=Path('/data/orders'); shutil.rmtree(p) if p.exists() else None"
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local
	@for image in examples/spark-local-orders:4.3.0 examples/spark-local-verifier:26.0.1; do if docker image inspect $$image >/dev/null 2>&1; then docker image rm $$image; fi; done
