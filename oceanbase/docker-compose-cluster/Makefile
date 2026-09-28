.PHONY: up test failover benchmark status cli cli-sys logs down

# `make benchmark`: sysbench OLTP; SMOKE=1 for a quick run
SMOKE ?=
BENCH_TIME ?= $(if $(SMOKE),10,60)
BENCH_TABLES ?= $(if $(SMOKE),2,4)
BENCH_SIZE ?= $(if $(SMOKE),10000,50000)
BENCH_THREADS ?= $(if $(SMOKE),4,1 8 32)
BENCH_WORKLOADS ?= oltp_point_select oltp_read_only oltp_read_write
# observer sysbench connects to: ob1 holds the leaders (PRIMARY_ZONE zone1;zone2;zone3)
export BENCH_HOST ?= ob1

# any observer answers for the whole cluster (followers forward to the leader)
OBCLIENT := docker exec -i ob1 obclient -h127.1 -P2881 -A --table

# Start the three observers (about 10 s until they listen), then bootstrap the cluster and
# create tenant `test` (scripts/bootstrap.sh, about 30 s; skipped when already done).
up:
	docker compose up --detach --wait --wait-timeout 300
	docker exec ob1 /scripts/bootstrap.sh

# sql/*.sql through obclient: plain files as root@test (MySQL-mode user tenant),
# *.sys.sql files as root@sys (cluster-wide views).
test:
	for f in sql/*.sql; do \
		case $$f in *.sys.sql) u=root@sys ;; *) u=root@test ;; esac; \
		echo "-- $$f (as $$u)"; cat $$f; echo "-- output:"; \
		$(OBCLIENT) -u$$u < $$f || exit 1; \
	done

# kill the observer with the leaders, show writes still commit and the leaders moved,
# start it again and wait for it to catch up (FAILOVER_NODE=ob2|ob3 to pick another)
failover:
	sh scripts/failover.sh

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 6
BENCH_MEM ?= 21g
BENCH_SPEC ?= ob1 ob2 ob3
export BENCH_CLIENT_CPUS ?= 2
export BENCH_NOTE ?= 2 CPUs and 7g per observer: memory_limit 6G (the smallest that bootstraps) plus process overhead; 3 x 8g does not fit a 24 GB Docker VM with the client

# prepare sbtest tables in the `test` tenant, run each workload x thread count for
# BENCH_TIME seconds, print TPS/QPS/latency, clean up; raw + JSON in results/
benchmark:
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; BENCH_TIME='$(BENCH_TIME)' BENCH_TABLES='$(BENCH_TABLES)' BENCH_SIZE='$(BENCH_SIZE)' \
		BENCH_THREADS='$(BENCH_THREADS)' BENCH_WORKLOADS='$(BENCH_WORKLOADS)' bench/run.sh; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	$(OBCLIENT) -uroot@sys -e "SELECT SVR_IP, ZONE, STATUS, WITH_ROOTSERVER, BUILD_VERSION FROM oceanbase.DBA_OB_SERVERS ORDER BY ZONE; \
		SELECT TENANT_ID, TENANT_NAME, TENANT_TYPE, STATUS, LOCALITY, PRIMARY_ZONE FROM oceanbase.DBA_OB_TENANTS; \
		SELECT TENANT_ID, LS_ID, ZONE, SVR_IP, ROLE FROM oceanbase.CDB_OB_LS_LOCATIONS WHERE TENANT_ID = 1002 ORDER BY LS_ID, ZONE"

cli:
	docker exec -it ob1 obclient -h127.1 -P2881 -uroot@test -A

cli-sys:
	docker exec -it ob1 obclient -h127.1 -P2881 -uroot@sys -A oceanbase

logs:
	for c in ob1 ob2 ob3; do echo "== $$c"; docker logs --tail 5 $$c 2>&1; \
		docker exec $$c sh -c 'grep -h "ERROR\|EDIAG" /root/ob/log/observer.log | tail -5 | cut -c1-300'; done

down:
	docker compose --profile bench down --volumes
