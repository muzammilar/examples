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
	@echo 'a-bucket objects by prefix (S3 ListObjectsV2, SigV4-signed by the rustfs container'"'"'s curl; first 1000 keys):'
	@docker compose exec -T rustfs curl -sS --aws-sigv4 aws:amz:us-east-1:s3 --user rustfsadmin:rustfsadmin \
	  'http://localhost:9000/a-bucket?list-type=2' | sed 's/<Contents>/|/g' | tr '|' '\n' \
	  | sed -n 's/.*<Key>\([^<]*\)<\/Key>.*<Size>\([0-9]*\)<\/Size>.*/\1 \2/p' \
	  | awk '{split($$1, p, "/"); k = p[1] "/" p[2]; n[k]++; b[k] += $$2} END {for (k in n) printf "  %-18s %5d objects %12d bytes\n", k, n[k], b[k]}' | sort

cli:
	docker compose run --rm tools python -i -c \
	  "from pymilvus import MilvusClient; client = MilvusClient('http://standalone:19530'); print('client =', client, client.list_collections())"

# needs `make up`; SMOKE=1 for a 10k-vector run, or set N / DIM / K
benchmark:
	@docker compose ps --status running --services | grep -qx standalone || { echo 'run `make up` first'; exit 1; }
	SMOKE=$(SMOKE) docker compose run --rm bench

down:
	docker compose --profile tools down --volumes --rmi local
