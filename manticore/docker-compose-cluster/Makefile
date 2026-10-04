.PHONY: up test failover scale-out scale-in status cli logs down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

# manticore-1..3, then CREATE CLUSTER c / JOIN CLUSTER and the tables (scripts/bootstrap.sh)
up:
	docker compose up --detach --wait
	sh scripts/bootstrap.sh

# cluster status; replicated table written on one node, read on another; sharded table
test:
	sh scripts/test.sh

# Go load (client/) on all nodes; manticore-2 is killed KILL_AFTER s in and started DOWN_FOR s
# later; every acknowledged row is then counted on every node (results/load.txt, events.txt)
failover:
	sh scripts/failover.sh

# 3 -> 5 nodes (manticore-4, -5) and back to 3, under load; STEADY s of load before and after
scale-out:
	sh scripts/scale.sh out

scale-in:
	sh scripts/scale.sh in

status:
	docker compose --profile scale ps --format 'table {{.Name}}\t{{.Status}}'
	@for c in $$(docker compose --profile scale ps --format '{{.Name}}' | grep -E 'manticore-cc-[0-9]$$'); do \
		docker exec $$c mysql -h127.0.0.1 -P9306 --skip-table -N -e "SHOW STATUS LIKE 'cluster_c_%'" | \
		awk -v c=$$c -F'\t' '$$1 ~ /_(status|size|node_state|indexes)$$/ { printf "%s %s=%s  ", c, $$1, $$2 } END { print "" }'; done
	docker exec manticore-cc-1 mysql -h127.0.0.1 -P9306 --table -e "SHOW SHARDING STATUS events; SHOW SHARDING MASTER"

cli:
	docker exec -it manticore-cc-1 mysql -h127.0.0.1 -P9306

logs:
	docker compose --profile scale logs --follow

down:
	docker compose --profile scale --profile load down --volumes --remove-orphans --rmi local
