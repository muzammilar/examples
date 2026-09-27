.PHONY: up test status cli down

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

down:
	docker compose down --volumes
