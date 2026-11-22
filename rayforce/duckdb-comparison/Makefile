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
	python3 -m unittest test_controller.py test_ingestion.py test_persistence.py test_live_journal.py test_journal_tail.py test_journal_corruption.py test_journal_header.py test_aof.py test_duckdb_recovery.py
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

.PHONY: test-journal-writer-lock
test-journal-writer-lock: check-controls
	JOURNAL_SCENARIO=writer-lock python3 live_journal.py

.PHONY: test-journal-writer-lock-bind test-lock-filesystem test-lock-filesystem-bind
test-journal-writer-lock-bind: check-controls
	JOURNAL_STORAGE=bind JOURNAL_SCENARIO=writer-lock python3 live_journal.py
test-lock-filesystem:
	python3 filesystem_lock_check.py volume
test-lock-filesystem-bind:
	python3 filesystem_lock_check.py bind

.PHONY: test-production report-journal
# Selected implemented cases; JOURNAL.md lists the remaining boundaries.
test-production: check-controls
	JOURNAL_STORAGE=volume JOURNAL_SCENARIO=process-crash python3 production.py

.PHONY: test-production-recovery
test-production-recovery: check-controls
	JOURNAL_STORAGE=volume JOURNAL_SCENARIO=process-crash python3 production.py --profile recovery

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

.PHONY: test-journal-tail-recovery
test-journal-tail-recovery: check-controls
	@test -n "$(JOURNAL_SOURCE)" || { echo 'Set JOURNAL_SOURCE to a completed journal-live result directory'; exit 1; }
	python3 journal_tail.py "$(JOURNAL_SOURCE)"

TAIL_RESULT ?=
.PHONY: report-journal-tail
report-journal-tail:
	@test -n "$(TAIL_RESULT)" || { echo 'Set TAIL_RESULT to an explicit journal-tail result directory'; exit 1; }
	python3 publish_tail_results.py "$(TAIL_RESULT)"

.PHONY: test-journal-interior-corruption
test-journal-interior-corruption: check-controls
	@test -n "$(JOURNAL_SOURCE)" || { echo 'Set JOURNAL_SOURCE to a completed journal-live result directory'; exit 1; }
	python3 journal_corruption.py "$(JOURNAL_SOURCE)"

CORRUPTION_RESULT ?=
.PHONY: report-journal-corruption
report-journal-corruption:
	@test -n "$(CORRUPTION_RESULT)" || { echo 'Set CORRUPTION_RESULT to an explicit journal-corruption directory'; exit 1; }
	python3 publish_corruption_results.py "$(CORRUPTION_RESULT)"

.PHONY: test-journal-header-corruption report-journal-header
test-journal-header-corruption: check-controls
	@test -n "$(JOURNAL_SOURCE)" || { echo 'Set JOURNAL_SOURCE to a completed journal-live result directory'; exit 1; }
	python3 journal_header.py "$(JOURNAL_SOURCE)"

HEADER_RESULT ?=
report-journal-header:
	@test -n "$(HEADER_RESULT)" || { echo 'Set HEADER_RESULT to an explicit journal-header directory'; exit 1; }
	python3 journal_header.py --publish "$(HEADER_RESULT)"

.PHONY: test-aof-integrity
test-aof-integrity: check-controls
	python3 aof.py

AOF_SEGMENT_BYTES ?= 65536
.PHONY: test-aof-segments
test-aof-segments: check-controls
	AOF_SEGMENT_LIMIT=$(AOF_SEGMENT_BYTES) python3 aof_segments.py

AOF_SEGMENT_RESULT ?=
.PHONY: report-aof-segments
report-aof-segments:
	@test -n "$(AOF_SEGMENT_RESULT)" || { echo 'Set AOF_SEGMENT_RESULT to an explicit aof-segments result directory'; exit 1; }
	python3 publish_aof_segments.py "$(AOF_SEGMENT_RESULT)"

AOF_RESULT ?=
.PHONY: report-aof
report-aof:
	@test -n "$(AOF_RESULT)" || { echo 'Set AOF_RESULT to an explicit AOF result directory'; exit 1; }
	python3 publish_aof.py "$(AOF_RESULT)"

.PHONY: test-duckdb-connectivity test-duckdb-recovery
test-duckdb-connectivity: check-controls
	python3 duckdb_live.py

test-duckdb-recovery: check-controls
	python3 duckdb_recovery.py

DUCKDB_RECOVERY_RESULT ?=
.PHONY: report-duckdb-recovery
report-duckdb-recovery:
	@test -n "$(DUCKDB_RECOVERY_RESULT)" || { echo 'Set DUCKDB_RECOVERY_RESULT to an explicit duckdb-live result directory'; exit 1; }
	python3 publish_duckdb_recovery.py "$(DUCKDB_RECOVERY_RESULT)"
