.PHONY: up test conflict benchmark status cli down

MYSQL := docker exec -i tidb-mysql mariadb -h tidb -P 4000 -u root --skip-ssl-verify-server-cert --table
PD_CTL := docker exec tidb-pd0 /pd-ctl -u http://pd0:2379
PD_JQ := docker exec -i tidb-pd0 jq -r
STORES_UP = $$($(PD_CTL) store | $(PD_JQ) '[.stores[] | select(.store.state_name == "Up")] | length')
UNDER_REPLICATED = $$($(PD_CTL) region | $(PD_JQ) '[.regions[] | select((.peers | length) < 3)] | length')

up:
	docker compose up --detach --wait
	@echo "==> waiting until PD sees 3 stores Up and every region has 3 replicas"
	for i in $$(seq 90); do [ "$(STORES_UP)" = 3 ] && [ "$(UNDER_REPLICATED)" = 0 ] && break; sleep 2; done
	[ "$(STORES_UP)" = 3 ] && [ "$(UNDER_REPLICATED)" = 0 ]
	$(MYSQL) -e 'SELECT type, instance, version FROM information_schema.cluster_info ORDER BY type, instance'

# sql/*.sql in order (schema + generated data, AUTO_RANDOM, regions, EXPLAIN ANALYZE, transactions), then conflicts
test:
	for f in sql/*.sql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(MYSQL) -v < $$f || exit 1; done
	$(MAKE) --no-print-directory conflict

# two concurrent sessions each: optimistic write conflict at COMMIT, pessimistic lock wait timeout
conflict:
	@echo "==> optimistic: A updates alice and sleeps, B updates alice and commits first; A's COMMIT fails"
	( sleep 1; $(MYSQL) < conflict/optimistic-fast.sql && echo "B: committed" ) & \
	  if $(MYSQL) < conflict/optimistic-slow.sql; then echo "A: unexpectedly committed"; wait; exit 1; fi; \
	  echo "A: rolled back (expected: Write conflict)"; wait
	@echo "==> pessimistic: A holds bob's row lock for 3 s, B gives up after 1 s"
	$(MYSQL) < conflict/pessimistic-holder.sql & \
	  sleep 1; if $(MYSQL) < conflict/pessimistic-waiter.sql; then echo "B: unexpectedly got the lock"; wait; exit 1; fi; \
	  echo "B: gave up (expected: Lock wait timeout)"; wait
	$(MYSQL) -e 'SELECT id, owner, balance FROM demo.accounts ORDER BY id'

# go-tpc TPC-C (WAREHOUSES warehouses) then sysbench oltp_point_select / oltp_read_write on
# TABLES tables of TABLE_SIZE rows, DURATION s each with THREADS connections (SMOKE=1: 10 s,
# 1 warehouse, 2 x 10,000 rows). Prints a summary table, keeps results/tidb-<UTC time>.{txt,json};
# drops the tpcc and sbtest databases afterwards.
SMOKE ?=
DURATION ?= $(if $(filter 1,$(SMOKE)),10,60)
THREADS ?= 8
WAREHOUSES ?= $(if $(filter 1,$(SMOKE)),1,4)
TABLES ?= $(if $(filter 1,$(SMOKE)),2,4)
TABLE_SIZE ?= $(if $(filter 1,$(SMOKE)),10000,50000)

benchmark:
	@docker compose ps --status running --services | grep -qx tidb || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@docker compose --progress quiet --profile bench build bench
	@export NAME=tidb-$$(date -u +%Y%m%dT%H%M%SZ) DURATION=$(DURATION) THREADS=$(THREADS) WAREHOUSES=$(WAREHOUSES) \
		TABLES=$(TABLES) TABLE_SIZE=$(TABLE_SIZE) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T bench && docker compose run --rm -T bench-report

status:
	$(MYSQL) -e 'SELECT type, instance, status_address, version, uptime FROM information_schema.cluster_info ORDER BY type, instance'
	$(MYSQL) -e 'SELECT store_id, address, store_state_name, leader_count, region_count, capacity, available FROM information_schema.tikv_store_status ORDER BY store_id'
	$(PD_CTL) member leader show | $(PD_JQ) '"PD leader: \(.name)"'
	$(PD_CTL) store | $(PD_JQ) '.stores[] | "store \(.store.id) \(.store.address) \(.store.state_name) leaders=\(.status.leader_count) regions=\(.status.region_count)"'

cli:
	docker exec -it tidb-mysql mariadb -h tidb -P 4000 -u root --skip-ssl-verify-server-cert

down:
	docker compose --profile bench down --volumes --rmi local
