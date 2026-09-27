.PHONY: up test status cli down

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

down:
	docker compose --profile tools down --volumes
