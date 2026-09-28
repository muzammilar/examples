.PHONY: up test benchmark status cli down

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

# sysbench oltp_point_select / oltp_read_only / oltp_read_write on TABLES TABLE_TYPE tables of
# TABLE_SIZE rows (RAND_TYPE key distribution), DURATION s each with THREADS clients, then RUNS
# timed runs of a few aggregation queries on the columnstore demo.orders (loaded by sql/01-02
# first if missing). SMOKE=1: 10 s, 2 x 10,000 rows, 3 runs. Prints a summary table, keeps
# results/singlestore-<UTC time>.{txt,json}; drops the sbtest database afterwards.
SMOKE ?=
DURATION ?= $(if $(filter 1,$(SMOKE)),10,60)
THREADS ?= 8
TABLES ?= $(if $(filter 1,$(SMOKE)),2,4)
TABLE_SIZE ?= $(if $(filter 1,$(SMOKE)),10000,50000)
TABLE_TYPE ?= rowstore
RAND_TYPE ?= uniform
RUNS ?= $(if $(filter 1,$(SMOKE)),3,5)

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= singlestore
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx singlestore || { echo 'run `make up` first'; exit 1; }
	@$(SQL) -N -e 'SELECT COUNT(*) FROM demo.orders' >/dev/null 2>&1 || { echo "==> loading demo.orders (sql/01-schema.sql, sql/02-generate.sql)"; \
		for f in sql/01-schema.sql sql/02-generate.sql; do $(SQL) < $$f >/dev/null || exit 1; done; }
	@mkdir -p results
	@docker compose --progress quiet --profile bench build bench
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=singlestore-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) THREADS=$(THREADS) TABLES=$(TABLES) \
		TABLE_SIZE=$(TABLE_SIZE) TABLE_TYPE=$(TABLE_TYPE) RAND_TYPE=$(RAND_TYPE) RUNS=$(RUNS) SINGLESTORE_PASSWORD=$(SINGLESTORE_PASSWORD) \
		HOST_INFO="$$(uname -sm)" SERVER_PLATFORM="$$(docker image inspect --format '{{.Os}}/{{.Architecture}}' $$(docker inspect --format '{{.Image}}' singlestore))" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T bench && docker compose run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps
	$(SQL) --table -e "SHOW LEAVES; SHOW DATABASES EXTENDED; \
		SELECT DATABASE_NAME, TABLE_NAME, STORAGE_TYPE, SUM(ROWS) AS \`rows\`, SUM(MEMORY_USE) AS memory_bytes \
		FROM information_schema.TABLE_STATISTICS GROUP BY 1, 2, 3 ORDER BY 1, 2"

cli:
	docker exec -it -e MYSQL_PWD=$(SINGLESTORE_PASSWORD) singlestore \
		/usr/lib/singlestore-client/singlestore-client --no-defaults --protocol=tcp -h 127.0.0.1 -P 3306 -u root --prompt='singlestore> '

down:
	docker compose --profile bench down --volumes --rmi local
