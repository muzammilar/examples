.PHONY: up test status cli benchmark down

SMOKE ?=

CYPHER := docker exec -i neo4j cypher-shell -u neo4j -p demo-password --format plain

up:
	docker compose up --detach --wait

# cypher/*.cypher in order: schema, MERGE nodes/relationships, paths, aggregation
test:
	for f in cypher/*.cypher; do echo "-- $$f"; cat $$f; echo "--"; $(CYPHER) < $$f || exit 1; done

status:
	$(CYPHER) "SHOW DATABASES YIELD name, currentStatus, role"

cli:
	docker exec -it neo4j cypher-shell -u neo4j -p demo-password

# needs `make up`; SMOKE=1 for 10k persons (default 100k), or set N
benchmark:
	@docker compose ps --status running --services | grep -qx neo4j || { echo 'run `make up` first'; exit 1; }
	SMOKE=$(SMOKE) HOST_UID=$$(id -u) HOST_GID=$$(id -g) docker compose run --rm bench

down:
	docker compose down --volumes
