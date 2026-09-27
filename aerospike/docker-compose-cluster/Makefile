.PHONY: up dashboards test failover status cli down

# upstream dashboards from aerospike-monitoring (release tag)
AEROSPIKE_MONITORING_REF := v3.20.0

# aql exits 0 even on errors: print its output, fail on an "Error" line or when $(1) is missing
AQL := docker compose run --rm -T tools aql -h aerospike-1 -f /dev/stdin
AQL_EXPECT = 2>&1 | awk -v need='$(1)' '{print} /^Error/{e=1} index($$0, need){ok=1} END{exit e || !ok}'

up: dashboards
	docker compose up --detach
	until docker exec aerospike-1 asinfo -v 'cluster-stable:size=3' 2>/dev/null | grep -qv ERROR; do sleep 1; done
	@echo "cluster stable, size 3"

# downloads into the gitignored grafana/provisioning/dashboards/upstream/ (skips existing files)
dashboards:
	grafana/fetch-dashboards.sh grafana/provisioning/dashboards/upstream \
		https://raw.githubusercontent.com/aerospike/aerospike-monitoring/$(AEROSPIKE_MONITORING_REF)/config/grafana/dashboards \
		cluster.json node.json namespace.json set.json latency.json

# writes, reads and deletes a few records in namespace test (RF=2), set demo
test:
	docker compose run --rm tools aql -h aerospike-1 -f /aql/test.aql

# stop aerospike-3: replicas get promoted, every record stays readable and writable; then bring it back
failover:
	@echo "==> writing 10 records (all three nodes up)"
	$(AQL) < failover/write-before.aql $(call AQL_EXPECT,record affected)
	docker exec aerospike-1 asadm -e 'info namespace object'
	@echo "==> stopping aerospike-3"
	docker compose stop aerospike-3
	@echo "==> waiting until the cluster re-forms at size 2"
	for i in $$(seq 30); do docker exec aerospike-1 asinfo -v 'cluster-stable:size=2' | grep -qv ERROR && break; sleep 1; done
	docker exec aerospike-1 asinfo -v statistics -l | grep -E '^cluster_size=2$$'
	docker exec aerospike-1 asadm -e 'info network'
	@echo "==> reading all 10 records (the surviving replicas are now masters)"
	$(AQL) < failover/read.aql $(call AQL_EXPECT,10 rows in set)
	@echo "==> writing 5 more records with one node down"
	$(AQL) < failover/write-during.aql $(call AQL_EXPECT,record affected)
	$(AQL) < failover/read.aql $(call AQL_EXPECT,15 rows in set)
	@echo "==> starting aerospike-3"
	docker compose start aerospike-3
	@echo "==> waiting for size 3 and for migrations to finish"
	for i in $$(seq 60); do docker exec aerospike-1 asinfo -v 'cluster-stable:size=3;ignore-migrations=false' | grep -qv ERROR && break; sleep 1; done
	docker exec aerospike-1 asinfo -v 'cluster-stable:size=3;ignore-migrations=false' | grep -v ERROR
	docker exec aerospike-1 asadm -e 'info namespace object'
	$(AQL) < failover/read.aql $(call AQL_EXPECT,15 rows in set)

status:
	docker exec aerospike-1 asadm -e 'info'

cli:
	docker exec -it aerospike-1 asadm

down:
	docker compose --profile tools down --volumes
