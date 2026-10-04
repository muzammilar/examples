.PHONY: aio-max-nr up run kafka-up kafka-run compare build status cli redpanda-stop kafka-stop down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

# the shared benchmark client and the repo's Kafka cluster (go/kafka-franz)
FRANZ := ../../go/kafka-franz
KF_COMPOSE := docker compose --project-directory $(FRANZ) -f $(FRANZ)/docker-compose.yml
KF_BROKERS := kafka-broker-1 kafka-broker-2 kafka-broker-3
KF_ALL := $(KF_BROKERS) kafka-controller-1 kafka-controller-2 kafka-controller-3
BENCH_IMAGE := redpanda-streaming-bench

# per broker, both systems
export BROKER_CPUS ?= 2
export BROKER_MEM ?= 3g
BENCH_CPUS ?= 4
# WRITE_CACHING=true: Redpanda acks once a majority has the batch in memory (Kafka-like)
WRITE_CACHING ?= false
# the two runs per system (short: a max-rate run writes duration x MiB/s x 3 replicas to the Docker disk)
LATENCY_ARGS ?= -rate 10000 -duration 30s -txns 100
MAX_ARGS ?= -rate 0 -duration 5s -warmup 1s
BENCH = docker run --rm --network $(1) --cpus $(BENCH_CPUS) $(BENCH_IMAGE) -brokers $(2) -label "$(3)"

build:
	docker build -q -t $(BENCH_IMAGE) --target bench $(FRANZ)

# Seastar asks for 10,000 AIO events per shard (3 brokers x 2 shards here); the Docker VM default
# fs.aio-max-nr is 65,536 for everything running. Not namespaced: on Docker Desktop this raises it
# for the whole VM until Docker restarts (on Linux: for the host).
AIO_MAX_NR ?= 1048576
aio-max-nr:
	@docker run --rm --privileged busybox:1.37 sh -c 'cur=$$(cat /proc/sys/fs/aio-max-nr); \
		if [ $(AIO_MAX_NR) -gt "$$cur" ]; then sysctl -w fs.aio-max-nr=$(AIO_MAX_NR); \
		else echo "fs.aio-max-nr = $$cur (fs.aio-nr = $$(cat /proc/sys/fs/aio-nr))"; fi'

# Redpanda: 3 brokers, capped
up: aio-max-nr
	docker compose up --detach --wait
	docker exec redpanda-sl-0 rpk cluster config set write_caching_default $(WRITE_CACHING)
	docker exec redpanda-sl-0 rpk cluster info -b

run: build
	@mkdir -p results
	@v=$$(docker exec redpanda-sl-0 rpk --version | awk '{print $$5}'); f=results/redpanda-wc-$(WRITE_CACHING)-$$(date -u +%Y%m%dT%H%M%SZ).txt; \
	for args in "$(LATENCY_ARGS)" "$(MAX_ARGS)"; do \
		$(call BENCH,redpanda-streaming_default,redpanda-0:9092$(,)redpanda-1:9092$(,)redpanda-2:9092,redpanda $$v write_caching=$(WRITE_CACHING)) $$args || exit 1; \
	done | tee $$f
, := ,

# Kafka: go/kafka-franz's brokers and controllers, brokers capped like Redpanda's
kafka-up:
	$(KF_COMPOSE) up --detach --wait $(KF_BROKERS)
	docker update --cpus $(BROKER_CPUS) --memory $(BROKER_MEM) --memory-swap $(BROKER_MEM) $(KF_BROKERS) >/dev/null
	@echo "capped $(KF_BROKERS) at $(BROKER_CPUS) CPUs / $(BROKER_MEM) each; controllers uncapped"

kafka-run: build
	@mkdir -p results
	@v=$$(docker exec kafka-broker-1 /opt/kafka/bin/kafka-topics.sh --version 2>/dev/null | awk '{print $$1}'); f=results/kafka-$$(date -u +%Y%m%dT%H%M%SZ).txt; \
	for args in "$(LATENCY_ARGS)" "$(MAX_ARGS)"; do \
		$(call BENCH,kafka-franz_kafka-network,kafka-broker-1:19092$(,)kafka-broker-2:19092$(,)kafka-broker-3:19092,kafka $$v) $$args || exit 1; \
	done | tee $$f

# all three, one after the other, each on a fresh cluster removed afterwards
compare: build
	$(MAKE) --no-print-directory up run redpanda-stop
	$(MAKE) --no-print-directory up run redpanda-stop WRITE_CACHING=true
	$(MAKE) --no-print-directory kafka-up kafka-run kafka-stop

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}'
	$(KF_COMPOSE) ps --format 'table {{.Name}}\t{{.Status}}'

cli:
	docker exec -it redpanda-sl-0 bash

redpanda-stop:
	docker compose down --volumes --remove-orphans

# removes the go/kafka-franz containers this example started (and that compose project's network)
kafka-stop:
	$(KF_COMPOSE) rm --stop --force $(KF_ALL)
	-docker network rm kafka-franz_kafka-network >/dev/null 2>&1

down: redpanda-stop
	-$(KF_COMPOSE) rm --stop --force $(KF_ALL) 2>/dev/null
	-docker network rm kafka-franz_kafka-network >/dev/null 2>&1
	-docker image rm $(BENCH_IMAGE) 2>/dev/null
