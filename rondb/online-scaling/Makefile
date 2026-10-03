.PHONY: help up load dist baseline add-nodegroup reorg after add-api drop-nodegroup scale-up all status cli down

MGM := docker exec rondb-scale-mgmd ndb_mgm --ndb-connectstring=mgmd:1186
MYSQL := docker exec -i rondb-scale-mysqld-1 mysql -uroot --table
COMPOSE := docker compose
BENCH := $(COMPOSE) --progress quiet --profile bench run --rm -T bench
# load phases: SECS per before/after measurement, REORG_SECS for the run that the reorganisation
# happens under (it starts REORG_DELAY s into that run)
SECS ?= 30
REORG_SECS ?= 120
REORG_DELAY ?= 20
TABLES ?= 4
export TABLES
# wait until data node $(1) reports "started"
WAIT_STARTED = for i in $$(seq 180); do $(MGM) -e "$(1) status" 2>/dev/null | grep -q ': started' && break; \
	[ $$i -lt 180 ] || { echo "node $(1) not started after 360 s"; exit 1; }; sleep 2; done
# epoch + text into results/events.txt (read by bench/series.sh)
EVENT = echo "$$(date +%s) $(1)" >> results/events.txt

help:
	@echo 'make all   runs every step below in order (~12 min):'
	@echo '  up              mgmd + node group 0 (data nodes 1, 2) + mysqld-1'
	@echo '  load            sysbench prepare: TABLES x TABLE_SIZE rows (ENGINE=NDB), then make dist'
	@echo '  baseline        oltp_read_write for SECS s, 2 data nodes'
	@echo '  add-nodegroup   activate nodes 3, 4; start them; CREATE NODEGROUP 3,4 (+ drop/create while empty)'
	@echo '  reorg           ALTER TABLE ... REORGANIZE PARTITION + OPTIMIZE TABLE under oltp_read_write load'
	@echo '  after           the baseline workload again on 4 data nodes'
	@echo '  add-api         start mysqld-2 + the REST API server, load through both MySQL Servers'
	@echo '  drop-nodegroup  try DROP NODEGROUP on the node group that now holds data'
	@echo '  scale-up        DataMemory 768M -> 1G and NumCPUs 2 -> 3, rolling restart under load'
	@echo '  dist / status / cli / down'

up:
	@mkdir -p results
	$(COMPOSE) up --detach --wait mgmd ndbd-1 ndbd-2
	$(COMPOSE) up --detach --wait mysqld-1
	$(MGM) -e show

load:
	$(COMPOSE) --progress quiet --profile bench build bench
	$(BENCH) prepare
	@$(MAKE) --no-print-directory dist

# where the rows are: fragments of sbtest1 per data node / node group, then memory per node
dist:
	$(MYSQL) -e "SELECT node_id, group_id AS node_group, status FROM ndbinfo.nodes JOIN ndbinfo.membership USING (node_id); \
		SELECT p.partition_name, p.table_rows FROM information_schema.partitions p \
		WHERE p.table_schema = 'sbtest' AND p.table_name = 'sbtest1' ORDER BY p.partition_ordinal_position; \
		SELECT m.node_id, COUNT(*) AS fragment_replicas, SUM(m.fixed_elem_count) AS rows_held \
		FROM ndbinfo.memory_per_fragment m WHERE m.fq_name = 'sbtest/def/sbtest1' GROUP BY m.node_id ORDER BY m.node_id; \
		SELECT node_id, ROUND(used / 1048576) AS data_mib_used, ROUND(total / 1048576) AS data_mib_total \
		FROM ndbinfo.memoryusage WHERE memory_type = 'Data memory' ORDER BY node_id"

baseline:
	$(BENCH) run 1-before-rw oltp_read_write $(SECS) mysqld-1 | tee results/summary.txt

# RonDB-specific: nodes 3 + 4 are in config.ini with NodeActive=0; activating them is a
# management-server command (no new config.ini, no rolling restart). They start --initial
# (empty volume) in no node group (NodeGroup=65536), then CREATE NODEGROUP makes them node
# group 1. A node group that holds no data can be dropped again, which is shown once.
add-nodegroup:
	$(MGM) -e "3 activate"
	$(MGM) -e "4 activate"
	$(COMPOSE) --profile scale up --detach --no-deps ndbd-3 ndbd-4
	@$(call WAIT_STARTED,3)
	@$(call WAIT_STARTED,4)
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/^$$/p'
	time $(MGM) -e "CREATE NODEGROUP 3,4"
	@echo "==> node group 1 is empty: it can be dropped and created again"
	$(MGM) -e "DROP NODEGROUP 1"
	$(MGM) -e "CREATE NODEGROUP 3,4"
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/^$$/p'
	@echo "==> existing tables still live in node group 0 only:"
	@$(MAKE) --no-print-directory dist

# oltp_read_write through mysqld-1 for REORG_SECS s; REORG_DELAY s in, every sbtest table is
# reorganised onto both node groups (online: ALGORITHM=INPLACE), then OPTIMIZE TABLE frees the
# moved rows' memory on nodes 1 + 2. The interval reports show the load around it.
reorg:
	@: > results/events.txt
	$(BENCH) run 2-during-reorg-rw oltp_read_write $(REORG_SECS) mysqld-1 > results/reorg-summary.txt & \
	sleep $(REORG_DELAY); \
	for t in $$(seq $(TABLES)); do \
		$(call EVENT,ALTER TABLE sbtest$$t REORGANIZE PARTITION start); \
		echo "ALTER TABLE sbtest$$t ALGORITHM=INPLACE, REORGANIZE PARTITION"; \
		$(MYSQL) -e "ALTER TABLE sbtest.sbtest$$t ALGORITHM=INPLACE, REORGANIZE PARTITION" || exit 1; \
		$(call EVENT,ALTER TABLE sbtest$$t done); \
	done; \
	for t in $$(seq $(TABLES)); do \
		$(MYSQL) -e "OPTIMIZE TABLE sbtest.sbtest$$t" >/dev/null || exit 1; \
	done; \
	$(call EVENT,OPTIMIZE TABLE x$(TABLES) done); \
	wait
	sh bench/series.sh results/2-during-reorg-rw.txt results/events.txt
	cat results/reorg-summary.txt | tee -a results/summary.txt
	@$(MAKE) --no-print-directory dist

after:
	$(BENCH) run 3-after-rw oltp_read_write $(SECS) mysqld-1 | tee -a results/summary.txt

# more API nodes: the spare [MYSQLD] slot 68 and [API] slot 195 in config.ini take a second
# MySQL Server and the REST API server, no restart of anything that runs
add-api:
	$(COMPOSE) --profile scale up --detach --wait --no-deps mysqld-2 rest
	$(MGM) -e show | sed -n '/mysqld(API)/,$$p'
	curl -sS --fail-with-body -X POST http://127.0.0.1:$${RONDB_REST_PORT:-4407}/0.1.0/sbtest/sbtest1/pk-read \
		-H 'Content-Type: application/json' -d '{"filters": [{"column": "id", "value": 42}], "readColumns": [{"column": "k"}]}'; echo
	$(BENCH) run 4-two-mysqld-rw oltp_read_write $(SECS) mysqld-1,mysqld-2 | tee -a results/summary.txt

# scaling in: DROP NODEGROUP only takes an empty node group, and nothing moves data out of
# one, so a node group that holds table fragments stays (expected to fail). Deactivating a
# node of it only stops that replica (node group 1 runs on node 4 alone); it is activated
# and started again (node restart from its volume).
drop-nodegroup:
	-$(MGM) -e "DROP NODEGROUP 1"
	-$(MGM) -e "3 deactivate"
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/^$$/p'
	$(MGM) -e "3 activate"
	$(COMPOSE) --profile scale up --detach --no-deps ndbd-3
	@$(call WAIT_STARTED,3)
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/^$$/p'

# vertical scaling: copy config/config-scale-up.ini (DataMemory=1G, NumCPUs=3) into the
# management server and restart it (--reload -> new config generation), give the data node
# containers 3 CPUs (docker update), then restart the data nodes one at a time ("N restart",
# never two of a node group together) while oltp_read_write runs through both MySQL Servers
scale-up:
	$(MGM) -e 'all report memory'
	docker cp config/config-scale-up.ini rondb-scale-mgmd:/srv/hops/mysql-cluster/config.ini
	docker restart rondb-scale-mgmd
	@for i in $$(seq 30); do $(MGM) -e show >/dev/null 2>&1 && break; sleep 2; done
	docker logs rondb-scale-mgmd 2>&1 | grep -iE 'reload|generation|config' | tail -n 4
	docker update --cpus 3 --memory 2560m --memory-swap 2560m rondb-scale-ndbd-1 rondb-scale-ndbd-2 rondb-scale-ndbd-3 rondb-scale-ndbd-4
	@: > results/events.txt
	$(BENCH) run 5-rolling-restart-rw oltp_read_write $(REORG_SECS) mysqld-1,mysqld-2 > results/rolling-summary.txt & \
	sleep 20; \
	for n in 1 2 3 4; do \
		$(call EVENT,node $$n restart); \
		$(MGM) -e "$$n restart" || exit 1; sleep 5; \
		$(call WAIT_STARTED,$$n); \
		$(call EVENT,node $$n started); \
	done; \
	wait
	sh bench/series.sh results/5-rolling-restart-rw.txt results/events.txt
	cat results/rolling-summary.txt | tee -a results/summary.txt
	$(MGM) -e 'all report memory'
	$(MYSQL) -e "SELECT node_id, COUNT(*) AS block_threads FROM ndbinfo.threads WHERE thread_name NOT IN ('main','rep') GROUP BY node_id; \
		SELECT node_id, thread_name FROM ndbinfo.threads WHERE node_id = 1"

all: up load baseline add-nodegroup reorg after add-api drop-nodegroup scale-up
	@echo; echo "==== results/summary.txt"; cat results/summary.txt

status:
	$(MGM) -e show
	$(MGM) -e 'all report memory'

cli:
	docker exec -it rondb-scale-mysqld-1 mysql -uroot

down:
	$(COMPOSE) --profile '*' down --volumes --rmi local
