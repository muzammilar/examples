.PHONY: up test status cli down

up:
	docker compose up --detach
	until docker exec aerospike-1 asinfo -v 'cluster-stable:size=3' 2>/dev/null | grep -qv ERROR; do sleep 1; done
	@echo "cluster stable, size 3"

# writes, reads and deletes a few records in namespace test (RF=2), set demo
test:
	docker compose run --rm tools aql -h aerospike-1 -f /aql/test.aql

status:
	docker exec aerospike-1 asadm -e 'info'

cli:
	docker exec -it aerospike-1 asadm

down:
	docker compose --profile tools down --volumes
