.PHONY: up test status cli failover verify scale-out scale-in scale-demo enterprise-ha-check logs down

MAIN := memgraph-main
MG = docker exec -i $(1) mgconsole --output-format=tabular

up:
	docker compose up --detach --wait main replica1 replica2
	docker compose up setup
	docker compose --profile tools build client

# write on MAIN, read on both replicas, then a write on a replica must be refused
test:
	@echo "-- memgraph-main: cypher/01-main-write.cypher"; $(call MG,$(MAIN)) < cypher/01-main-write.cypher
	@for r in memgraph-replica-1 memgraph-replica-2; do echo "-- $$r: cypher/02-replica-read.cypher"; \
		$(call MG,$$r) < cypher/02-replica-read.cypher || exit 1; done
	@echo "-- write on memgraph-replica-1 (must be refused)"
	@if echo "CREATE (:Account {id: 0});" | $(call MG,memgraph-replica-1) 2>&1 | tee /dev/stderr | grep -q 'forbidden on the replica'; \
		then echo "OK: refused"; else echo "FAIL: replica accepted a write"; exit 1; fi

ROLE = echo 'SHOW REPLICATION ROLE;' | docker exec -i $(1) mgconsole --output-format=csv 2>/dev/null | tail -n +2 | tr -d '"'

status:
	@for c in memgraph-main memgraph-replica-1 memgraph-replica-2 memgraph-replica-3 memgraph-replica-4; do \
		docker inspect $$c >/dev/null 2>&1 || continue; printf '%-20s %s\n' $$c "$$($(call ROLE,$$c) || echo down)"; done
	@for c in memgraph-main memgraph-replica-1 memgraph-replica-2; do \
		[ "$$($(call ROLE,$$c))" = main ] && { echo "-- SHOW REPLICAS on $$c (MAIN)"; echo "SHOW REPLICAS;" | $(call MG,$$c); }; done; true

cli:
	docker exec -it $(MAIN) mgconsole

# kill MAIN under write load, compare replicas with acknowledged writes, promote the SYNC replica
# by hand, bring the old MAIN back and demote it. Afterwards: make down && make up.
failover:
	sh scripts/failover.sh

# every acknowledged write of the last `make failover` on every instance
verify:
	docker compose --profile tools run --rm --no-deps client verify

# read replicas: add memgraph-replica-3 and -4 (ASYNC), then remove them again
scale-out:
	sh scripts/scale.sh out

scale-in:
	sh scripts/scale.sh in

# read load over the replicas while scaling 2 -> 4 -> 2 replicas (180 s)
scale-demo:
	sh scripts/scale.sh demo

# starts one coordinator (Enterprise HA) without a license and shows the refusal
enterprise-ha-check:
	docker compose --profile ha up --detach --wait coordinator
	@echo "SHOW INSTANCES;" | docker exec -i memgraph-coordinator-1 mgconsole 2>&1 || true
	docker compose --profile ha rm --stop --force coordinator

logs:
	docker compose logs --follow main replica1 replica2

down:
	docker compose --profile tools --profile ha --profile scale down --volumes --remove-orphans --rmi local
