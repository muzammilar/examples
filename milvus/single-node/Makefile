.PHONY: up test status cli benchmark down

SMOKE ?=

up:
	docker compose up --detach --wait
	curl -s localhost:9091/healthz; echo

# collection (dim 4), insert + flush, HNSW index build + load, k-NN, expr-filtered k-NN, range search
test:
	docker compose run --rm tools python scripts/test.py

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}'
	curl -s -X POST localhost:19530/v2/vectordb/collections/list -H 'Content-Type: application/json' -d '{}'; echo

cli:
	docker compose run --rm tools python -i -c \
	  "from pymilvus import MilvusClient; client = MilvusClient('http://standalone:19530'); print('client =', client, client.list_collections())"

# needs `make up`; SMOKE=1 for a 10k-vector run, or set N / DIM / K
benchmark:
	@docker compose ps --status running --services | grep -qx standalone || { echo 'run `make up` first'; exit 1; }
	SMOKE=$(SMOKE) docker compose run --rm bench

down:
	docker compose --profile tools down --volumes --rmi local
