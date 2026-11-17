SHELL := /bin/sh
ROWS ?= 2000000
REPEATS ?= 15
TRIALS ?= 3
THREADS ?= 1
CPUS ?= $(THREADS)
export ROWS REPEATS TRIALS THREADS CPUS
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
	python3 -m unittest test_controller.py
benchmark:
	python3 experiment.py benchmark
down:
	python3 experiment.py down
docker-down:
	python3 experiment.py docker-down
