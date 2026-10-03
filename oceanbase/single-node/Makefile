.PHONY: aio-max-nr up test benchmark status cli cli-sys logs down

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
# obd refuses to start an observer when the kernel's async-I/O limit is nearly used up (ScyllaDB
# and other OceanBase stacks on the same Docker VM use it too). Raise fs.aio-max-nr if it is lower
# than AIO_MAX_NR. Not namespaced: on Docker Desktop this is the whole Docker VM until Docker
# restarts; on a Linux host it changes the host kernel. `AIO_MAX_NR=0` leaves it unchanged.
AIO_MAX_NR ?= 1048576
aio-max-nr:
	@docker run --rm --privileged busybox:1.37 sh -c 'cur=$$(cat /proc/sys/fs/aio-max-nr); \
		if [ $(AIO_MAX_NR) -gt "$$cur" ]; then sysctl -w fs.aio-max-nr=$(AIO_MAX_NR); \
		else echo "fs.aio-max-nr = $$cur (fs.aio-nr = $$(cat /proc/sys/fs/aio-nr))"; fi'

up: aio-max-nr
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

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 8g
BENCH_SPEC ?= oceanbase
export BENCH_CLIENT_CPUS ?= 2
export BENCH_NOTE ?= memory raised from 6g to 8g: the observer memory_limit is 6G (the smallest that bootstraps) plus process overhead

# prepare sbtest tables in the `test` tenant, run each workload x thread count for
# BENCH_TIME seconds, print TPS/QPS/latency, clean up; raw + JSON in results/
benchmark:
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; BENCH_TIME='$(BENCH_TIME)' BENCH_TABLES='$(BENCH_TABLES)' BENCH_SIZE='$(BENCH_SIZE)' \
		BENCH_THREADS='$(BENCH_THREADS)' BENCH_WORKLOADS='$(BENCH_WORKLOADS)' bench/run.sh; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

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
