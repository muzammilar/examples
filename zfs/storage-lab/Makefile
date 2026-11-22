SHELL := /bin/sh
COMPOSE := docker compose
LOCAL_UID := $(shell id -u)
LOCAL_GID := $(shell id -g)
DISK_GIB ?= 16
DATA_MIB ?= 512
WRITE_MIB_S ?= 4
BENCH_MIB ?= 256
BENCH_SECONDS ?= 10
RESULTS_DIR ?= results/manual
SETUP ?= all
export LOCAL_UID LOCAL_GID DISK_GIB DATA_MIB WRITE_MIB_S BENCH_MIB BENCH_SECONDS RESULTS_DIR
RUN := python3 run.py
.NOTPARALLEL:
.PHONY: report test-production unit-test build keys docker-up up test mirror-test raidz2-test replication-test benchmark status cli down docker-down
keys:
	@mkdir -p state/ssh "$(RESULTS_DIR)"
	@test -f state/ssh/id_ed25519 || ssh-keygen -q -t ed25519 -N '' -f state/ssh/id_ed25519
build: keys
	$(COMPOSE) build source

docker-up: build
	$(RUN) docker-up
up:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) up
unit-test:
	python3 -m unittest test_resources.py test_lifecycle.py test_report.py test_observe_process.py
	$(COMPOSE) run --rm --entrypoint python3 runner -m unittest discover -s /opt/lab/app -p 'test_*.py'

test: unit-test
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) test "$(SETUP)"
test-production:
	$(MAKE) test SETUP=all
mirror-test:
	$(MAKE) test SETUP=mirror
raidz2-test:
	$(MAKE) test SETUP=raidz2
replication-test:
	$(MAKE) test SETUP=replication
benchmark:
	@mkdir -p "$(RESULTS_DIR)"
	$(RUN) benchmark
status:
	$(COMPOSE) ps --all
	$(RUN) status
cli:
	$(COMPOSE) exec source ssh -i /keys/id_ed25519 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -p 2222 lab@127.0.0.1
down:
	$(RUN) down
docker-down:
	$(RUN) docker-down

report:
	python3 report.py "$(RESULTS_DIR)"
