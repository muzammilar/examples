.PHONY: up test status cli down

up:
	docker compose up --detach --wait
	curl -s localhost:8080/v1/meta | jq -c '{hostname, version}'

# collection (dim 4, cosine, no vectorizer), batch insert, nearVector, filtered nearVector, BM25, hybrid
test:
	docker compose run --rm tools sh scripts/test.sh

status:
	curl -s 'localhost:8080/v1/nodes?output=verbose' | jq -c '.nodes[] | {name, status, version, stats}'
	curl -s localhost:8080/v1/schema | jq -c '[.classes[].class]'

cli:
	docker compose run --rm tools sh

down:
	docker compose --profile tools down --volumes
