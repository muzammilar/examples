.PHONY: up test status cli down

up:
	docker compose up --detach --wait
	@for i in $$(seq 60); do \
		docker exec aerospike asinfo -v 'cluster-stable:size=1' 2>/dev/null | grep -qv ERROR && { echo "cluster stable"; exit 0; }; \
		sleep 1; \
	done; echo "node not stable after 60 s (docker compose logs aerospike)"; exit 1

# empty set test.users (truncate), add a numeric secondary index on bin age, then run aql/test.aql
test:
	docker exec aerospike asinfo -v 'truncate:namespace=test;set=users'
	docker exec aerospike asadm --enable -e 'manage sindex create numeric users_age ns test set users bin age'
	docker compose run --rm tools aql -h aerospike -u /aql -f /aql/test.aql

status:
	docker exec aerospike asadm -e 'info'

cli:
	docker exec -it aerospike asadm

down:
	docker compose --profile tools down --volumes
