.PHONY: up test failover status cli down

# arangosh on coordinator-1 (js/ and failover/ are mounted there)
ARANGOSH := docker exec -i arangodb-coordinator-1 arangosh --server.password demo --quiet

up:
	docker compose up --detach --wait
	$(ARANGOSH) --javascript.execute /js/01-health.js

# each js/0*.js asserts what it shows: health, a 3-shard RF=2 collection and its
# shard distribution, AQL through the coordinator, a graph traversal over sharded collections
test:
	for f in js/0*.js; do echo "-- $$f"; $(ARANGOSH) --javascript.execute /$$f || exit 1; done

# stop the DB-server leading the first shard of `events`: supervision marks it FAILED and promotes
# the followers, reads + writes go on through the coordinator; then start it and wait for GOOD + in sync
failover:
	@set -e; echo "==> collection events: 3 shards, replicationFactor 2, 100 documents"; \
	out=$$($(ARANGOSH) --javascript.execute /failover/setup.js); echo "$$out"; \
	set -- $$(echo "$$out" | tail -n 1); victim=$$1; \
	echo "==> stopping $$victim"; \
	docker compose stop $$victim; \
	echo "==> with $$victim down"; \
	$(ARANGOSH) --javascript.execute /failover/during.js $$*; \
	echo "==> starting $$victim"; \
	docker compose up --detach --wait $$victim; \
	$(ARANGOSH) --javascript.execute /failover/after.js $$*

status:
	curl -s -u root:demo 127.0.0.1:8539/_api/version; echo
	$(ARANGOSH) --javascript.execute-string "require('/js/lib.js').printHealth()"

cli:
	docker exec -it arangodb-coordinator-1 arangosh --server.password demo --server.database demo

down:
	docker compose down --volumes
