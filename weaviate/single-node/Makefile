.PHONY: up test status cli benchmark down

SMOKE ?=

up:
	docker compose up --detach --wait
	curl -s localhost:8080/v1/meta | jq -c '{hostname, version}'

# collection (dim 4, cosine, no vectorizer), batch insert, nearVector, filtered nearVector, BM25, hybrid
test:
	docker compose run --rm tools sh scripts/test.sh

# node status, collections, and the live Landmark count (GraphQL Aggregate; the
# per-shard objectCount in /v1/nodes?output=verbose is refreshed lazily and lags)
status:
	curl -s localhost:8080/v1/nodes | jq -c '.nodes[] | {name, status, version}'
	curl -s localhost:8080/v1/schema | jq -c '[.classes[].class]'
	curl -s localhost:8080/v1/graphql -H 'Content-Type: application/json' \
	  -d '{"query": "{Aggregate{Landmark{meta{count}}}}"}' | jq -c '.errors // {Landmark: .data.Aggregate.Landmark[0].meta.count}'

cli:
	docker compose run --rm tools sh

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= weaviate
export BENCH_CLIENT_CPUS ?= 2

# needs `make up`; SMOKE=1 for a 10k-vector run, or set N / DIM / K
benchmark:
	@docker compose ps --status running --services | grep -qx weaviate || { echo 'run `make up` first'; exit 1; }
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; SMOKE=$(SMOKE) docker compose run --rm bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

down:
	docker compose --profile tools down --volumes --rmi local
