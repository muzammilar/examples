.PHONY: up test status cli down

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

down:
	docker compose down --volumes
