.PHONY: up test scale-out scale-in demo resize status cli cli-sys logs down

# any observer answers for the whole cluster (followers forward to the leader)
OBCLIENT := docker exec -i ob1 obclient -h127.1 -P2881 -A --table

# Start ob1..ob3 (one per zone), bootstrap the cluster and create tenant `test`
# (scripts/bootstrap.sh, skipped when already done), then obproxy.
# ob4..ob6 are only started by scale-out.
up:
	docker compose up --detach --wait --wait-timeout 300 ob1 ob2 ob3
	docker exec ob1 /scripts/bootstrap.sh
	docker compose up --detach obproxy
	for i in $$(seq 1 60); do docker exec ob1 obclient -hobproxy -P2883 -uroot@test\#obcluster -A -e 'SELECT 1' >/dev/null 2>&1 && break; sleep 1; done
	docker exec ob1 obclient -hobproxy -P2883 -uroot@test\#obcluster -A -e "SELECT 'obproxy ready' AS status"

# sql/*.sql: plain files as root@test, *.sys.sql as root@sys
test:
	for f in sql/*.sql; do \
		case $$f in *.sys.sql) u=root@sys ;; *) u=root@test ;; esac; \
		echo "-- $$f (as $$u)"; cat $$f; echo "-- output:"; \
		$(OBCLIENT) -u$$u < $$f || exit 1; \
	done

# start ob4..ob6, ADD SERVER, UNIT_NUM 2, watch the log stream split and partition transfer
scale-out:
	scripts/scale.sh out

# UNIT_NUM 1 (dropping the unit group on ob4..ob6), DELETE SERVER, remove ob4..ob6
scale-in:
	scripts/scale.sh in

# sysbench load (LOAD_WORKLOAD, LOAD_THREADS, through LOAD_HOST=obproxy or ob1) running
# through scale-out and scale-in, STEADY seconds before, between and after; tps per phase
demo:
	scripts/scale.sh demo

# tenant scale up/down with ALTER RESOURCE UNIT (RESIZE_STEPS="CPU:MEM ..."), sysbench at each size
resize:
	scripts/resize.sh

status:
	scripts/scale.sh status

cli:
	docker exec -it ob1 obclient -h127.1 -P2881 -uroot@test -A

cli-sys:
	docker exec -it ob1 obclient -h127.1 -P2881 -uroot@sys -A oceanbase

logs:
	for c in $$(docker compose --profile scale ps --format '{{.Name}}' | grep '^ob'); do echo "== $$c"; docker logs --tail 5 $$c 2>&1; \
		docker exec $$c sh -c 'grep -h "ERROR\|EDIAG" /root/ob/log/observer.log /root/ob/log/rootservice.log | tail -5 | cut -c1-300'; done

down:
	docker compose --profile scale --profile bench down --volumes
