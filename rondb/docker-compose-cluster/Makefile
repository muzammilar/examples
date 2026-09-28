.PHONY: up test failover status cli down

RONDB_REST_PORT ?= 4406
MGM := docker exec rondb-mgmd ndb_mgm --ndb-connectstring=mgmd:1186
MYSQL := docker exec -i rondb-mysqld mysql -uroot --table
# REST API pk-read of demo.accounts row $(1)
PK_READ = curl -sS --fail-with-body -X POST http://localhost:$(RONDB_REST_PORT)/0.1.0/demo/accounts/pk-read \
	-H 'Content-Type: application/json' \
	-d '{"filters": [{"column": "id", "value": $(1)}], "readColumns": [{"column": "owner"}, {"column": "balance"}]}'; echo

up:
	docker compose up --detach --wait
	$(MGM) -e show

# sql/*.sql through the MySQL Server, then the NDB API view of the table (ndb_desc)
# and a primary-key read through the REST API server
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(MYSQL) < $$f || exit 1; done
	@echo; echo "==== ndb_desc -d demo accounts -pn: fragments, rows per fragment, nodes holding each"
	docker exec rondb-mgmd ndb_desc --ndb-connectstring=mgmd:1186 -d demo accounts -pn \
		| sed -n '/^-- Per partition info/,$$p'
	@echo; echo "==== REST API: pk-read of demo.accounts id=3"
	$(call PK_READ,3)

# stop data node 2: node 1 holds a replica of every fragment, so reads and writes go on;
# node 2 then restarts, recovers locally and copies the changes it missed from node 1
failover:
	@echo "==> stopping data node 2 (rondb-ndbd-2)"
	docker compose stop ndbd-2
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/ndb_mgmd/p'
	@echo "==> write + read with one data node"
	$(MYSQL) -e "UPDATE demo.accounts SET balance = balance + 1 WHERE id = 5; \
		SELECT node_id, status FROM ndbinfo.nodes; SELECT * FROM demo.accounts WHERE id = 5;"
	$(call PK_READ,5)
	@echo "==> starting data node 2 (node restart: local recovery, then catch-up from node 1)"
	docker compose up --detach --wait ndbd-2
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/ndb_mgmd/p'
	docker compose logs ndbd-2 | grep -E 'Not initial start|Start phase 110 completed \(|has completed its restart' | tail -n 3
	@echo "==> both nodes hold every fragment again"
	$(MYSQL) -e "SELECT node_id, fragment_num, fixed_elem_count AS row_count FROM ndbinfo.memory_per_fragment \
		WHERE fq_name = 'demo/def/accounts' ORDER BY fragment_num, node_id; SELECT * FROM demo.accounts WHERE id = 5;"

status:
	$(MGM) -e show
	$(MGM) -e 'all report memory'

cli:
	docker exec -it rondb-mysqld mysql -uroot

down:
	docker compose down --volumes
