.PHONY: aio-max-nr up run status cli logs down

SYS := docker exec -i oceanbase-htap obclient -h127.1 -P2881 -uroot@sys -A --table
TEST := docker exec -i oceanbase-htap obclient -h127.1 -P2881 -uroot@test -A --table

# Bootstrap (obd deploy + start + tenant `test`) takes 1-5 minutes; the healthcheck passes once
# root@test connects, the entrypoint prints "boot success!" after its last step. Then build the app.
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
		log=$$(docker logs oceanbase-htap 2>&1); \
		case "$$log" in *'boot success!'*) echo 'boot success!'; break ;; *'boot failed!'*|*'[ERROR]'*) echo "$$log" | tail -20; exit 1 ;; esac; \
		sleep 5; \
	done
	@# mini sizes the memstore at ~25% of the tenant: the freeze trigger then sits above the
	@# write-throttling threshold and bulk loads crawl; 50% puts the freeze trigger first
	$(SYS) -e "ALTER SYSTEM SET memstore_limit_percentage = 50"
	docker compose --profile app build app

# app/main.go: load ROWS orders into a hybrid row/column table, compare row- vs column-store
# analytics, run OLTP alone / with row-store analytics / with column-store analytics, probe
# freshness. Output also in results/. ROWS, DURATION, OLTP_WORKERS, OLAP_WORKERS, DOP override.
run:
	@mkdir -p results
	docker compose --profile app run --build --rm -T app | tee results/htap-$$(date -u +%Y%m%dT%H%M%SZ).txt

status:
	$(SYS) -e "SELECT SVR_IP, ZONE, STATUS, BUILD_VERSION FROM oceanbase.DBA_OB_SERVERS; SELECT TENANT_ID, TENANT_NAME, TENANT_TYPE, COMPATIBILITY_MODE, STATUS FROM oceanbase.DBA_OB_TENANTS"
	$(TEST) -e "SELECT TABLE_NAME, ROUND(SUM(DATA_SIZE)/1024/1024, 1) AS DATA_MB FROM oceanbase.DBA_OB_TABLE_SPACE_USAGE WHERE DATABASE_NAME = 'htap' GROUP BY TABLE_NAME"

cli:
	docker exec -it oceanbase-htap obclient -h127.1 -P2881 -uroot@test -A htap

logs:
	docker logs --tail 50 oceanbase-htap

down:
	docker compose --profile app down --volumes --rmi local
