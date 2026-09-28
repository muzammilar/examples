.PHONY: up test status cli down

SINGLESTORE_PASSWORD ?= SingleStore-Demo-1
# the image's `singlestore` wrapper needs getopt (not installed); call the client binary directly
SQL := docker exec -i -e MYSQL_PWD=$(SINGLESTORE_PASSWORD) singlestore \
	/usr/lib/singlestore-client/singlestore-client --no-defaults --protocol=tcp -h 127.0.0.1 -P 3306 -u root

up:
	docker compose up --detach --wait
	$(SQL) --table -e 'SELECT @@memsql_version AS version; SHOW AGGREGATORS; SHOW LEAVES'

# the client does not echo statements, so each sql/*.sql file is printed before its output
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(SQL) --table < $$f || exit 1; done

status:
	docker compose ps
	$(SQL) --table -e "SHOW LEAVES; SHOW DATABASES EXTENDED; \
		SELECT DATABASE_NAME, TABLE_NAME, STORAGE_TYPE, SUM(ROWS) AS \`rows\`, SUM(MEMORY_USE) AS memory_bytes \
		FROM information_schema.TABLE_STATISTICS GROUP BY 1, 2, 3 ORDER BY 1, 2"

cli:
	docker exec -it -e MYSQL_PWD=$(SINGLESTORE_PASSWORD) singlestore \
		/usr/lib/singlestore-client/singlestore-client --no-defaults --protocol=tcp -h 127.0.0.1 -P 3306 -u root --prompt='singlestore> '

down:
	docker compose down --volumes
