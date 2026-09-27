.PHONY: up test status cli down

export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

COMPOSE := docker compose --progress quiet --profile tools
DUCKDB := $(COMPOSE) run --rm -T duckdb

up:
	$(COMPOSE) pull
	mkdir -p data
	test -f data/events.csv || $(COMPOSE) run --rm gen

# the duckdb CLI does not echo statements, so each sql/*.sql file is printed before
# its output; all files share data/demo.duckdb (01 creates the views the rest use)
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(DUCKDB) demo.duckdb < $$f || exit 1; done

status:
	$(DUCKDB) -c "SELECT version() AS duckdb, platform FROM pragma_platform()" \
		-c "SELECT 'events.csv' AS file, count(*) AS rows FROM 'events.csv' \
		    UNION ALL SELECT 'users.json', count(*) FROM 'users.json' \
		    UNION ALL SELECT 'countries.csv', count(*) FROM 'countries.csv'" \
		-c "SELECT split_part(filename[7:], '/', 1) AS path, count(*) AS files, \
		    format_bytes(sum(size)::BIGINT) AS size FROM read_blob('/data/**') GROUP BY ALL ORDER BY path"

cli:
	$(COMPOSE) run --rm duckdb demo.duckdb

down:
	$(COMPOSE) down --volumes --remove-orphans
	rm -rf data
