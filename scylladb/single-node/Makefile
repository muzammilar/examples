.PHONY: up test status cli down

up:
	docker compose up --detach --wait
	docker exec scylla nodetool status

# cqlsh does not echo statements, so each cql/*.cql file is printed before its output
test:
	for f in cql/*.cql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		docker exec scylla cqlsh --file /$$f || exit 1; done

status:
	docker exec scylla nodetool status

cli:
	docker exec -it scylla cqlsh

down:
	docker compose down --volumes
