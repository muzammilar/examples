.PHONY: up test failover status cli down

CYPHER := docker exec -i neo4j-1 cypher-shell -u neo4j -p demo-password --format plain

# Neo4j Enterprise Edition needs its license accepted; this example never does it for you.
# Export NEO4J_ACCEPT_LICENSE_AGREEMENT=eval (evaluation terms) or =yes (you hold a commercial license).
up:
	@case "$$NEO4J_ACCEPT_LICENSE_AGREEMENT" in \
	eval|yes) ;; \
	*) echo 'Refusing to start: this cluster runs Neo4j Enterprise Edition, which requires accepting its license.'; \
	   echo ''; \
	   echo '  export NEO4J_ACCEPT_LICENSE_AGREEMENT=eval   # accept the evaluation agreement: https://neo4j.com/terms/enterprise_us/'; \
	   echo '  export NEO4J_ACCEPT_LICENSE_AGREEMENT=yes    # you hold a commercial license:   https://neo4j.com/terms/licensing/'; \
	   echo ''; \
	   echo "NEO4J_ACCEPT_LICENSE_AGREEMENT is currently '$$NEO4J_ACCEPT_LICENSE_AGREEMENT'. See README.md#license."; \
	   exit 1 ;; \
	esac >&2
	docker compose up --detach --wait
	./cluster.sh wait 180

# SHOW SERVERS / SHOW DATABASE (3 primaries, one writer), cypher/*.cypher through neo4j:// routing,
# CREATE DATABASE demo2 TOPOLOGY 3 PRIMARIES; every step asserted
test:
	./cluster.sh test

# stop the leader of `neo4j`: a new one is elected and routed writes still work; start it again
# and wait until 3 servers are Available, every database is online and it has the new write
failover:
	./cluster.sh failover

status:
	$(CYPHER) -d system "SHOW SERVERS YIELD name, address, state, health, hosting ORDER BY address"
	$(CYPHER) -d system "SHOW DATABASES YIELD name, address, role, writer, currentStatus ORDER BY name, address"

# routing connection: writes go to the leader, read transactions (:begin with --access-mode read) to followers
cli:
	docker exec -it neo4j-1 cypher-shell -u neo4j -p demo-password -a neo4j://neo4j-1:7687

down:
	docker compose down --volumes
