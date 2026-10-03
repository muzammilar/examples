.PHONY: up test failover switchover sync async benchmark status cli cli-ro down

SHELL := bash
PSQL := docker compose --progress quiet run --rm --no-TTY psql
PSQL_RO := docker compose --progress quiet run --rm --no-TTY -e PGPORT=5001 psql
# patronictl on any running node (they all read the same state from etcd)
PATRONICTL = $$(for n in tsdb1 tsdb2 tsdb3; do docker exec $$n true 2>/dev/null && { echo docker exec $$n patronictl -c /config/patroni.yml; break; }; done)

up:
	docker compose up --detach --wait
	@$(PATRONICTL) list
	$(PSQL) -Atc "SELECT version()" -c "SELECT extname || ' ' || extversion FROM pg_extension ORDER BY 1"

# sql/01 on the primary (HAProxy :5000), then sql/02 on a replica (HAProxy :5001)
test:
	@echo "==== sql/01-primary.sql (HAProxy :5000 -> primary)"; cat sql/01-primary.sql; echo "---- output"
	$(PSQL) -f /sql/01-primary.sql
	@echo; echo "==== sql/02-replica.sql (HAProxy :5001 -> replicas)"; cat sql/02-replica.sql; echo "---- output"
	$(PSQL_RO) -f /sql/02-replica.sql

# stop the primary under a stream of inserts, watch the promotion and the old primary rejoin
DURATION ?= 60
STOP_AFTER ?= 10
failover:
	DURATION=$(DURATION) STOP_AFTER=$(STOP_AFTER) bash failover.sh

# planned: hand the primary role to a replica (TO=tsdbN, default: any healthy replica)
TO ?=
switchover:
	@$(PATRONICTL) switchover --force $(if $(TO),--candidate $(TO))
	@sleep 3; $(PATRONICTL) list

# synchronous_mode: a commit waits until one replica has the WAL (no acknowledged write lost on failover)
sync:
	@$(PATRONICTL) edit-config --force -s synchronous_mode=true >/dev/null
	@until $(PATRONICTL) list | grep -q 'Sync Standby'; do sleep 1; done; $(PATRONICTL) list
async:
	@$(PATRONICTL) edit-config --force -s synchronous_mode=false >/dev/null
	@while $(PATRONICTL) list | grep -q 'Sync Standby'; do sleep 1; done; $(PATRONICTL) list

# make benchmark: DEVICES x DAYS x 1/minute rows ingested through HAProxy with WORKERS
# timescaledb-parallel-copy workers, into the rowstore and straight into the columnstore, once with
# asynchronous and once with synchronous replication (MODES); then a pgbench read query with CLIENTS
# clients for BENCH_SECONDS against the primary alone and against the two replicas
SMOKE ?=
DEVICES ?= 1000
DAYS ?= $(if $(SMOKE),1,2)
WORKERS ?= 4
BATCH ?= 5000
MODES ?= async sync
CLIENTS ?= 8
BENCH_SECONDS ?= $(if $(SMOKE),5,20)

# resource budget (bench/limits.sh): BENCH_CPUS / BENCH_MEM split equally over BENCH_SPEC
BENCH_CPUS ?= 6
BENCH_MEM ?= 12g
BENCH_SPEC ?= tsdb1 tsdb2 tsdb3
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx haproxy || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=timescaledb-cluster-$$(date -u +%Y%m%dT%H%M%SZ) DEVICES=$(DEVICES) DAYS=$(DAYS) \
		WORKERS=$(WORKERS) BATCH=$(BATCH) MODES="$(MODES)" CLIENTS=$(CLIENTS) DURATION=$(BENCH_SECONDS) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose --progress quiet run --rm -T bench && docker compose --progress quiet run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	@$(PATRONICTL) list
	$(PSQL) -c "SELECT application_name AS replica, client_addr, state, sync_state, replay_lag, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)) AS behind FROM pg_stat_replication ORDER BY 1"

cli:
	docker compose run --rm psql
cli-ro:
	docker compose run --rm -e PGPORT=5001 psql

down:
	docker compose --profile tools --profile bench down --volumes
