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

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= standalone etcd=0.25:256m rustfs=0.25:512m
export BENCH_CLIENT_CPUS ?= 2

# needs `make up`; SMOKE=1 for a 10k-vector run, or set N / DIM / K
benchmark:
	@docker compose ps --status running --services | grep -qx standalone || { echo 'run `make up` first'; exit 1; }
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; SMOKE=$(SMOKE) docker compose run --rm bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

down:
	docker compose --profile tools down --volumes --rmi local
