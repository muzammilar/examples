.PHONY: up test benchmark status cli cli-sys logs down

# `make benchmark`: sysbench OLTP; SMOKE=1 for a quick run
SMOKE ?=
BENCH_TIME ?= $(if $(SMOKE),10,60)
BENCH_TABLES ?= $(if $(SMOKE),2,4)
BENCH_SIZE ?= $(if $(SMOKE),10000,50000)
BENCH_THREADS ?= $(if $(SMOKE),4,1 8 32)
BENCH_WORKLOADS ?= oltp_point_select oltp_read_only oltp_read_write

OBCLIENT := docker exec -i oceanbase obclient -h127.1 -P2881 -A --table

# Bootstrap (obd deploy + start + tenant creation) takes 1-5 minutes. The healthcheck
# passes once root@test connects; the entrypoint prints "boot success!" after its last
# step (tenant time zone data, init scripts), so wait for that too.
up:
	docker compose up --detach --wait --wait-timeout 900
	@for i in $$(seq 1 120); do \
		log=$$(docker logs oceanbase 2>&1); \
		case "$$log" in *'boot success!'*) echo 'boot success!'; exit 0 ;; *'boot failed!'*|*'[ERROR]'*) echo "$$log" | tail -20; exit 1 ;; esac; \
		sleep 5; \
	done; echo 'timed out waiting for "boot success!"'; exit 1

# sql/*.sql through obclient: plain files as root@test (MySQL-mode user tenant),
# *.sys.sql files as root@sys (cluster-wide views).
test:
	for f in sql/*.sql; do \
		case $$f in *.sys.sql) u=root@sys ;; *) u=root@test ;; esac; \
		echo "-- $$f (as $$u)"; cat $$f; echo "-- output:"; \
		$(OBCLIENT) -u$$u < $$f || exit 1; \
	done

# prepare sbtest tables in the `test` tenant, run each workload x thread count for
# BENCH_TIME seconds, print TPS/QPS/latency, clean up; raw + JSON in results/
benchmark:
	BENCH_TIME='$(BENCH_TIME)' BENCH_TABLES='$(BENCH_TABLES)' BENCH_SIZE='$(BENCH_SIZE)' \
		BENCH_THREADS='$(BENCH_THREADS)' BENCH_WORKLOADS='$(BENCH_WORKLOADS)' bench/run.sh

status:
	$(OBCLIENT) -uroot@sys -e "SELECT SVR_IP, ZONE, STATUS, BUILD_VERSION FROM oceanbase.DBA_OB_SERVERS; SELECT TENANT_ID, TENANT_NAME, TENANT_TYPE, COMPATIBILITY_MODE, STATUS FROM oceanbase.DBA_OB_TENANTS"

cli:
	docker exec -it oceanbase obclient -h127.1 -P2881 -uroot@test -A

cli-sys:
	docker exec -it oceanbase obclient -h127.1 -P2881 -uroot@sys -A oceanbase

logs:
	docker logs --tail 50 oceanbase

down:
	docker compose down --volumes
