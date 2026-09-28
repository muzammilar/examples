.PHONY: up test status cli benchmark down

SMOKE ?=

up:
	docker compose up --detach --wait
	curl -s localhost:6333; echo

# collection (dim 4, cosine), payload index, upsert, k-NN, filtered k-NN, recommend
test:
	docker compose run --rm tools sh scripts/test.sh

status:
	curl -s localhost:6333/telemetry | jq -c '.result | {version: .app.version, collections: .collections.number_of_collections}'
	curl -s localhost:6333/collections | jq -c '.result.collections'

cli:
	docker compose run --rm tools sh

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= qdrant
export BENCH_CLIENT_CPUS ?= 2

# needs `make up`; SMOKE=1 for a 10k-vector run, or set N / DIM / K
benchmark:
	@docker compose ps --status running --services | grep -qx qdrant || { echo 'run `make up` first'; exit 1; }
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; SMOKE=$(SMOKE) docker compose run --rm bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

down:
	docker compose --profile tools down --volumes --rmi local
