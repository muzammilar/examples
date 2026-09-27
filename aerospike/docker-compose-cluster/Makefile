.PHONY: up dashboards test status cli down

# upstream dashboards from aerospike-monitoring (release tag)
AEROSPIKE_MONITORING_REF := v3.20.0

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

status:
	docker exec aerospike-1 asadm -e 'info'

cli:
	docker exec -it aerospike-1 asadm

down:
	docker compose --profile tools down --volumes
