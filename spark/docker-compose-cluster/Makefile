SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name spark-order-recovery
.NOTPARALLEL:
.PHONY: docker-up up test verify worker-recovery down docker-down status
docker-up:
	$(COMPOSE) build master parquet-verifier
	$(COMPOSE) up -d --wait master worker
up:
	mkdir -p results
	$(COMPOSE) exec -T master python3 /opt/spark/lab-scripts/wait_workers.py > results/topology.json
	$(COMPOSE) exec -T --user root master bash -c 'mkdir -p /data/events && chown -R spark:spark /data'
test:
	mkdir -p results
	$(COMPOSE) run --rm --use-aliases orders > results/orders.log 2>&1
	$(MAKE) verify
worker-recovery:
	python3 scripts/worker_recovery.py
	$(MAKE) verify
verify:
	$(COMPOSE) run --rm --use-aliases orders /opt/spark/bin/spark-submit --master spark://master:7077 --driver-memory 1g --executor-memory 1g --total-executor-cores 2 --conf spark.driver.host=orders --conf spark.sql.shuffle.partitions=8 /opt/spark/orders-workload/orders.py verify > results/reopen.log 2>&1
	$(COMPOSE) run --rm parquet-verifier > results/parquet.json
status:
	$(COMPOSE) ps
down:
	$(COMPOSE) exec -T master python3 -c "import shutil; from pathlib import Path; p = Path('/data/orders'); shutil.rmtree(p) if p.exists() else None"
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local
