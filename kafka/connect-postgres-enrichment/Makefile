COMPOSE := docker compose -f docker-compose.yml
COUNT ?= 2000000
CUSTOMER_COUNT ?= 10000
TIMEOUT_SECONDS ?= 1800
CONNECT_PORT ?= 8083
POSTGRES_PORT ?= 55432
KAFKA_BROKER_1_PORT ?= 29092
KAFKA_BROKER_2_PORT ?= 39092
KAFKA_BROKER_3_PORT ?= 49092
PROCESSOR_PORT ?= 8080
export CONNECT_PORT POSTGRES_PORT KAFKA_BROKER_1_PORT KAFKA_BROKER_2_PORT KAFKA_BROKER_3_PORT PROCESSOR_PORT CUSTOMER_COUNT
CONNECT_URL := http://127.0.0.1:$(CONNECT_PORT)
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)

.PHONY: docker-up up kafka-topics connector processor demo test status logs cli down docker-down

docker-up:
	$(COMPOSE) up --build --detach --wait kafka-controller-1 kafka-controller-2 kafka-controller-3 \
		kafka-broker-1 kafka-broker-2 kafka-broker-3 postgres connect-1 connect-2

up: docker-up
	$(MAKE) kafka-topics
	$(MAKE) connector
	$(COMPOSE) up --build --detach --wait processor

kafka-topics:
	$(COMPOSE) exec -T kafka-broker-1 /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-broker-1:19092 --create --if-not-exists --topic orders \
		--partitions 6 --replication-factor 3 --config cleanup.policy=delete
	$(COMPOSE) exec -T kafka-broker-1 /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-broker-1:19092 --create --if-not-exists --topic orders.enriched \
		--partitions 6 --replication-factor 3 --config cleanup.policy=delete
	$(COMPOSE) exec -T kafka-broker-1 /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-broker-1:19092 --create --if-not-exists --topic pg.public.customers \
		--partitions 6 --replication-factor 3 --config cleanup.policy=compact

connector:
	curl --fail --silent --show-error --retry 30 --retry-delay 2 --retry-connrefused \
		-X PUT -H 'Content-Type: application/json' \
		--data-binary @connectors/postgres-customers.json \
		$(CONNECT_URL)/connectors/postgres-customers/config
	@for i in $$(seq 60); do \
		status=$$(curl -fsS $(CONNECT_URL)/connectors/postgres-customers/status 2>/dev/null | jq -r '.connector.state + ":" + ([.tasks[].state] | unique | join(","))' 2>/dev/null || true); \
		[ "$$status" = 'RUNNING:RUNNING' ] && { echo "Debezium connector and task are RUNNING"; exit 0; }; \
		sleep 2; \
	done; curl -fsS $(CONNECT_URL)/connectors/postgres-customers/status; exit 1

processor:
	$(COMPOSE) up --build --detach --wait processor

demo:
	$(COMPOSE) run --rm --build -e COUNT=$(COUNT) -e CUSTOMER_COUNT=$(CUSTOMER_COUNT) -e RUN_ID=$(RUN_ID) producer
	$(COMPOSE) run --rm --build -e EXPECTED_COUNT=$(COUNT) -e TIMEOUT_SECONDS=$(TIMEOUT_SECONDS) -e RUN_ID=$(RUN_ID) consumer

test: demo

status:
	$(COMPOSE) ps
	curl -fsS $(CONNECT_URL)/connectors/postgres-customers/status | jq
	curl -fsS http://127.0.0.1:8080/ready
	$(COMPOSE) exec -T kafka-broker-1 /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-broker-1:19092 --describe --topic orders.enriched

logs:
	$(COMPOSE) logs --tail=100 kafka-broker-1 connect-1 connect-2 postgres processor

cli:
	$(COMPOSE) exec postgres psql -U admin -d enrichment

down:
	-$(COMPOSE) stop processor
	-$(COMPOSE) rm --force processor

docker-down:
	$(COMPOSE) --profile apps down --volumes --remove-orphans --rmi local
