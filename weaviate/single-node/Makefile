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

# needs `make up`; SMOKE=1 for a 10k-vector run, or set N / DIM / K
benchmark:
	@docker compose ps --status running --services | grep -qx weaviate || { echo 'run `make up` first'; exit 1; }
	SMOKE=$(SMOKE) docker compose run --rm bench

down:
	docker compose --profile tools down --volumes --rmi local
