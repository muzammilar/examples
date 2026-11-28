SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
PYTHON ?= python3
ORDER_COUNT ?= 1000000
CUSTOMER_COUNT ?= 10000
export ORDER_COUNT CUSTOMER_COUNT
.NOTPARALLEL:
.PHONY: check docker-up up test verify executor-failure datanode-failure test-production observe-repair benchmark resources status down docker-down
check:
	$(PYTHON) -m unittest discover -s tests
	docker compose --project-name hdfs-spark-doris config --quiet
	docker compose --project-name hdfs-spark-doris build oracle
docker-up up test verify executor-failure datanode-failure observe-repair benchmark resources down docker-down:
	$(PYTHON) scripts/lifecycle.py $@
test-production:
	$(MAKE) executor-failure
	$(MAKE) datanode-failure
	$(MAKE) verify
status:
	docker compose --project-name hdfs-spark-doris ps

.PHONY: report
report:
	$(PYTHON) scripts/curate.py
