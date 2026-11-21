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
	python3 -m unittest test_controller.py test_ingestion.py test_persistence.py test_live_journal.py
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

.PHONY: test-persistence-capability test-duckdb-snapshot test-rayforce-snapshot
test-persistence-capability: check-controls
	python3 persistence.py capability
test-duckdb-snapshot: check-controls
	python3 persistence.py duckdb-snapshot
test-rayforce-snapshot: check-controls
	python3 persistence.py rayforce-snapshot

RAY_RESULT ?=
DUCK_RESULT ?=
.PHONY: report-persistence
report-persistence:
	@test -n "$(RAY_RESULT)" -a -n "$(DUCK_RESULT)" || { echo 'Set RAY_RESULT and DUCK_RESULT to explicit result.json paths'; exit 1; }
	python3 write_persistence_results.py "$(RAY_RESULT)" "$(DUCK_RESULT)"

.PHONY: test-journal-capability
test-journal-capability: check-controls
	python3 journal.py

.PHONY: test-journal-recovery
test-journal-recovery: check-controls
	python3 live_journal.py

.PHONY: test-production report-journal
# Selected implemented cases; JOURNAL.md lists the remaining boundaries.
test-production: test-journal-capability test-journal-recovery

JOURNAL_RESULT ?=
report-journal:
	@test -n "$(JOURNAL_RESULT)" || { echo 'Set JOURNAL_RESULT to an explicit journal-live result directory'; exit 1; }
	python3 write_journal_results.py "$(JOURNAL_RESULT)"

JOURNAL_SOURCE ?=
.PHONY: test-journal-restore
test-journal-restore: check-controls
	@test -n "$(JOURNAL_SOURCE)" || { echo 'Set JOURNAL_SOURCE to a completed journal-live result directory'; exit 1; }
	python3 journal_restore.py "$(JOURNAL_SOURCE)"

RESTORE_RESULT ?=
.PHONY: report-journal-restore
report-journal-restore:
	@test -n "$(RESTORE_RESULT)" || { echo 'Set RESTORE_RESULT to an explicit journal-restore result directory'; exit 1; }
	python3 journal_restore.py --publish "$(RESTORE_RESULT)"
