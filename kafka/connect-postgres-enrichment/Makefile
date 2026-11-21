COUNT ?= 2000000
CUSTOMER_COUNT ?= 10000
CONNECT_PORT ?= 8083
POSTGRES_PORT ?= 55432
KAFKA_BROKER_1_PORT ?= 29092
KAFKA_BROKER_2_PORT ?= 39092
KAFKA_BROKER_3_PORT ?= 49092
PROCESSOR_PORT ?= 8080
export CONNECT_PORT POSTGRES_PORT KAFKA_BROKER_1_PORT KAFKA_BROKER_2_PORT KAFKA_BROKER_3_PORT PROCESSOR_PORT CUSTOMER_COUNT

CONTROL := python3 enrichment_lifecycle.py

.PHONY: docker-up up kafka-topics connector processor demo test status logs cli down docker-down
.PHONY: prepare-workload workload-images probe-schema verify-history

docker-up up kafka-topics connector status logs cli down docker-down workload-images probe-schema verify-history:
	$(CONTROL) $@

processor: up

prepare-workload:
	python3 prepare_workload.py

# The witnessed baseline uses independent live readers and replays every ACK.
test: check-controls
	COUNT=$(COUNT) python3 enrichment_control.py baseline

demo: test

.NOTPARALLEL:
.PHONY: check-controls test-production chaos-broker chaos-connect chaos-processor chaos-postgres benchmark reconcile-prior inspect-compression
check-controls:
	python3 -m unittest -v test_enrichment.py test_lifecycle.py

test-production: check-controls
	COUNT=$(COUNT) python3 enrichment_control.py test-production

chaos-broker: check-controls
	COUNT=$(COUNT) python3 enrichment_control.py broker

chaos-connect: check-controls
	COUNT=$(COUNT) python3 enrichment_control.py connect

chaos-processor: check-controls
	COUNT=$(COUNT) python3 enrichment_control.py processor

chaos-postgres: check-controls
	COUNT=$(COUNT) python3 enrichment_control.py postgres

benchmark: check-controls
	COUNT=$(COUNT) python3 enrichment_control.py benchmark

reconcile-prior: check-controls
	CASE_ID=$(CASE_ID) python3 enrichment_control.py reconcile-prior

inspect-compression:
	uv run --script inspect_compression.py $(SEGMENTS)
