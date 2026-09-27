.PHONY: up test status cli down

PSQL := docker compose --progress quiet run --rm --no-TTY psql

up:
	docker compose up --detach --wait
	$(PSQL) -Atc 'SELECT version()'

# psql does not echo statements, so each sql/*.sql file is printed before its output
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(PSQL) -f /$$f || exit 1; done

status:
	docker compose ps
	$(PSQL) -c "SELECT relname AS table, pg_size_pretty(pg_total_relation_size(oid)) AS size FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace ORDER BY 1"

cli:
	docker compose run --rm psql

down:
	docker compose down --volumes
