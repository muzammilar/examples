.PHONY: up test status cli down

up:
	docker compose up --detach
	until docker exec aerospike asinfo -v 'cluster-stable:size=1' 2>/dev/null | grep -qv ERROR; do sleep 1; done
	@echo "cluster stable"

# writes, reads and deletes a few records in namespace test, set demo
test:
	docker compose run --rm tools aql -h aerospike -f /aql/test.aql

status:
	docker exec aerospike asadm -e 'info'

cli:
	docker exec -it aerospike asadm

down:
	docker compose --profile tools down --volumes
