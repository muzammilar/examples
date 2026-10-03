.PHONY: up load run failover status cli down

MGM := docker exec rondb-fs-mgmd ndb_mgm --ndb-connectstring=mgmd:1186
MYSQL := docker exec -i rondb-fs-mysqld mysql -uroot --table
CLIENT := docker compose run --rm -T client

USERS ?= 1000000
BATCH ?= 16
CONCURRENCY ?= 16
DURATION ?= 20s
MODES ?= sql-single,sql-in,sql-join,rest-pk,rest-batch
FLAGS = -users $(USERS) -batch $(BATCH) -concurrency $(CONCURRENCY)

up:
	docker compose up --detach --wait
	docker compose --progress quiet --profile client build client
	$(MGM) -e show

# create fs.user_profile_1 / fs.user_activity_1 (ENGINE=NDB) and load USERS rows into each
load:
	$(CLIENT) load $(FLAGS)
	$(MYSQL) -e "SELECT node_id, memory_type, ROUND(used/1048576) AS used_mib, ROUND(total/1048576) AS total_mib \
		FROM ndbinfo.memoryusage WHERE memory_type = 'Data memory'"

# serve feature vectors (BATCH users x 2 feature groups) for DURATION per mode with
# CONCURRENCY clients; loads the data first if it is not there yet
run: load
	$(MYSQL) -e "EXPLAIN SELECT p.age, a.clicks_7d FROM fs.user_profile_1 p JOIN fs.user_activity_1 a \
		ON a.user_id = p.user_id WHERE p.user_id IN (1, 2, 3)" | sed -n '1,6p'
	$(CLIENT) run $(FLAGS) -duration $(DURATION) -modes $(MODES)

# serve for 90 s (half sql-in, half rest-batch); stop data node 2 at ~15 s and start it
# again 30 s later (node restart: local recovery, then catch-up from node 1)
failover: load
	@( sleep 15; echo "==> $$(date -u +%H:%M:%S) docker stop rondb-fs-ndbd-2"; docker stop rondb-fs-ndbd-2 >/dev/null; \
	   sleep 30; echo "==> $$(date -u +%H:%M:%S) docker start rondb-fs-ndbd-2"; \
	   docker compose up --detach --wait ndbd-2 2>/dev/null; \
	   echo "==> $$(date -u +%H:%M:%S) data node 2 started (healthy) again" ) & \
	$(CLIENT) failover $(FLAGS) -duration 90s; wait
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/ndb_mgmd/p'

status:
	$(MGM) -e show
	$(MGM) -e 'all report memory'

cli:
	docker exec -it rondb-fs-mysqld mysql -uroot fs

down:
	docker compose --profile client down --volumes --rmi local
