.PHONY: up test restart benchmark status cli down

MGM := docker exec rondb-single-mgmd ndb_mgm --ndb-connectstring=mgmd:1186
MYSQL := docker exec -i rondb-single-mysqld mysql -uroot --table

up:
	docker compose up --detach --wait
	$(MGM) -e show
	@docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}' \
		rondb-single-mgmd rondb-single-ndbd rondb-single-mysqld

# sql/*.sql through the MySQL Server, then the NDB API view of the table (ndb_desc)
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(MYSQL) < $$f || exit 1; done
	@echo; echo "==== ndb_desc -d demo accounts -pn: fragments, rows per fragment, the node holding each"
	docker exec rondb-single-mgmd ndb_desc --ndb-connectstring=mgmd:1186 -d demo accounts -pn \
		| sed -n '/^-- Per partition info/,$$p'

# restart the only data node: SQL fails while it is down (no other replica), then the
# node recovers the rows from its own checkpoint + redo log (system restart)
restart:
	@echo "==> stopping the only data node"
	docker compose stop ndbd
	@echo "==> with the data node down:"
	-$(MYSQL) -e "SELECT * FROM demo.accounts WHERE id = 1"
	docker compose up --detach --wait ndbd
	@echo "==> waiting for mysqld to reconnect"
	@for i in $$(seq 60); do docker exec rondb-single-mysqld mysql -uroot -Nse \
		"SHOW STATUS LIKE 'Ndb_number_of_ready_data_nodes'" 2>/dev/null | awk '$$2 > 0 {ok=1} END {exit !ok}' && break; \
		[ $$i -lt 60 ] || { echo 'mysqld did not reconnect in 120 s'; exit 1; }; sleep 2; done
	$(MYSQL) -e "SELECT * FROM demo.accounts WHERE id <= 4 ORDER BY id"

# sysbench oltp_point_select / oltp_read_only / oltp_read_write on TABLES ENGINE=NDB tables of
# TABLE_SIZE rows; DURATION s each with THREADS clients (SMOKE=1: 10 s, 2 x 10,000 rows).
# Prints a summary table, keeps results/rondb-single-<UTC time>.{txt,json}; drops sbtest afterwards.
SMOKE ?=
DURATION ?= $(if $(filter 1,$(SMOKE)),10,30)
THREADS ?= 8
TABLES ?= 2
TABLE_SIZE ?= $(if $(filter 1,$(SMOKE)),10000,50000)
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx mysqld || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@docker compose --progress quiet --profile bench build bench
	@export NAME=rondb-single-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) THREADS=$(THREADS) TABLES=$(TABLES) \
		TABLE_SIZE=$(TABLE_SIZE) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T bench && docker compose run --rm -T bench-report

status:
	$(MGM) -e show
	$(MGM) -e 'all report memory'

cli:
	docker exec -it rondb-single-mysqld mysql -uroot

down:
	docker compose --profile bench down --volumes --rmi local
