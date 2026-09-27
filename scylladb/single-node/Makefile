.PHONY: up test status cli down

up:
	docker compose up --detach --wait
	docker exec scylla nodetool status

# RF=1 keyspace, a few writes/reads at ONE
test:
	docker exec scylla cqlsh --file /cql/test.cql

status:
	docker exec scylla nodetool status

cli:
	docker exec -it scylla cqlsh

down:
	docker compose down --volumes
