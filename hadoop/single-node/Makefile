SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
COMPOSE := docker compose --project-name hadoop-single-node
.NOTPARALLEL:
.PHONY: docker-up up test down docker-down status
docker-up:
	$(COMPOSE) build namenode
	$(COMPOSE) up -d --wait namenode datanode
up:
	mkdir -p results
	$(COMPOSE) run --rm files python3 /opt/hadoop/lab-scripts/wait_ready.py > results/topology.json
	$(COMPOSE) exec -T namenode hdfs dfs -mkdir -p /lab
test:
	$(COMPOSE) run --rm files > results/correctness.json
	$(COMPOSE) exec -T namenode hdfs fsck /lab -files -blocks -locations > results/fsck.log
status:
	$(COMPOSE) ps
	$(COMPOSE) exec -T namenode hdfs dfsadmin -report
down:
	$(COMPOSE) exec -T namenode hdfs dfs -rm -r -f -skipTrash /lab
docker-down:
	$(COMPOSE) --profile tools down --volumes --remove-orphans --rmi local

.PHONY: verify datanode-restart
verify:
	$(COMPOSE) run --rm files python3 /opt/hadoop/lab-scripts/files.py verify > results/readback.json
datanode-restart:
	$(COMPOSE) kill --signal SIGKILL datanode
	docker inspect hadoop-single-node-datanode-1 --format '{{json .State}}' > results/killed-datanode.json
	$(COMPOSE) up --no-deps -d --wait datanode
	$(COMPOSE) run --rm files python3 /opt/hadoop/lab-scripts/wait_ready.py > results/recovered-topology.json
	$(MAKE) verify
