.PHONY: up build run status cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

up:
	docker compose up --detach --wait
	docker exec manticore-ls mysql -h127.0.0.1 -P9306 --skip-table -N -e 'SHOW VERSION' | head -n 1
	docker exec manticore-ls-elasticsearch curl -s localhost:9200 | grep '"number"'

build:
	docker compose --profile run build app

# app/: ingest the same DOCS log lines into both engines, sizes, correctness, query latency;
# then each engine's data directory size and container memory. Output: results/run-<UTC>.txt
run:
	@mkdir -p results
	@f=results/run-$$(date -u +%Y%m%dT%H%M%SZ).txt; \
	{ docker compose --progress quiet --profile run run --rm --build app; rc=$$?; \
	echo; echo "data directories (du) and container memory after the run:"; \
	docker exec manticore-ls du -sh /var/lib/manticore/logs; \
	docker exec manticore-ls-elasticsearch du -sh /usr/share/elasticsearch/data/indices; \
	docker stats --no-stream --format '{{.Name}}: {{.MemUsage}}' manticore-ls manticore-ls-elasticsearch; exit $$rc; } 2>&1 | tee $$f

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec manticore-ls mysql -h127.0.0.1 -P9306 --table -e 'SHOW TABLES'
	docker exec manticore-ls-elasticsearch curl -s 'localhost:9200/_cat/indices?v'

cli:
	docker exec -it manticore-ls mysql -h127.0.0.1 -P9306

down:
	docker compose --profile run down --volumes --remove-orphans --rmi local
