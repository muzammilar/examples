.PHONY: up test status cli benchmark down

SMOKE ?=

ARANGOSH := docker exec -i arangodb arangosh --server.password demo --quiet

up:
	docker compose up --detach --wait
	$(ARANGOSH) --javascript.execute /aql/schema.js

# each aql/*.aql is one AQL query, run against database "demo"
test:
	for f in aql/*.aql; do echo "-- $$f"; cat $$f; \
	  $(ARANGOSH) --server.database demo --javascript.execute-string \
	    "db._query(require('fs').read('/$$f')).toArray().forEach(r => print(JSON.stringify(r)))" || exit 1; done

status:
	curl -s -u root:demo localhost:8529/_api/version?details=false; echo
	curl -s -u root:demo localhost:8529/_admin/server/availability; echo

cli:
	docker exec -it arangodb arangosh --server.password demo --server.database demo

# needs `make up`; SMOKE=1 for 10k persons (default 100k), or set N
benchmark:
	@docker compose ps --status running --services | grep -qx arangodb || { echo 'run `make up` first'; exit 1; }
	SMOKE=$(SMOKE) HOST_UID=$$(id -u) HOST_GID=$$(id -g) docker compose run --rm bench

down:
	docker compose down --volumes
