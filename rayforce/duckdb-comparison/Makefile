SHELL := /bin/sh
ROWS ?= 2000000
REPEATS ?= 15
TRIALS ?= 3
THREADS ?= 1
CPUS ?= $(THREADS)
MUTATION_ROWS ?= 10000
BATCH_SIZES ?= 1,100,1000,10000
METHODS ?= query,native
LANGUAGES ?= python,cpp,rust
export ROWS REPEATS TRIALS THREADS CPUS MUTATION_ROWS BATCH_SIZES METHODS LANGUAGES
.NOTPARALLEL:
.PHONY: docker-up up test benchmark check-controls down docker-down
# Engines are embedded in each isolated workload, with no resident server.
docker-up:
	python3 prepare.py
	docker compose build analytics
up:
	python3 experiment.py prepare
test: check-controls
	python3 experiment.py test
check-controls:
	python3 -m unittest test_controller.py test_ingestion.py
benchmark:
	python3 experiment.py benchmark
down:
	python3 experiment.py down
docker-down:
	python3 experiment.py docker-down

.PHONY: test-ingestion ingestion-benchmark
test-ingestion: check-controls
	python3 ingestion.py test
ingestion-benchmark: check-controls
	python3 ingestion.py benchmark
