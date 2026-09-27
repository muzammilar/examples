.PHONY: up test status cli down

up:
	docker compose up --detach --wait
	docker exec scylla-1 nodetool status

# RF=3 keyspace, a few writes/reads at QUORUM
test:
	docker exec scylla-1 cqlsh --file /cql/test.cql

status:
	docker exec scylla-1 nodetool status

cli:
	docker exec -it scylla-1 cqlsh

down:
	docker compose down --volumes
