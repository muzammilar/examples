.PHONY: up dashboards test status cli down

# upstream dashboards (prebuilt for ScyllaDB $(SCYLLA_DASH_VERSION)) and the recording rules they rely on
SCYLLA_MONITORING_REF := 4.16.1
SCYLLA_DASH_VERSION := 2026.1
SCYLLA_MONITORING := https://raw.githubusercontent.com/scylladb/scylla-monitoring/$(SCYLLA_MONITORING_REF)

up: dashboards
	docker compose up --detach --wait
	docker exec scylla-1 nodetool status

# downloads into the gitignored grafana/provisioning/dashboards/upstream/ and prometheus/upstream/
dashboards:
	grafana/fetch-dashboards.sh grafana/provisioning/dashboards/upstream $(SCYLLA_MONITORING)/grafana/build/ver_$(SCYLLA_DASH_VERSION) \
		$(foreach d,overview detailed cql os,scylla-$(d).$(SCYLLA_DASH_VERSION).json)
	grafana/fetch-dashboards.sh prometheus/upstream $(SCYLLA_MONITORING)/prometheus/prom_rules prometheus.latency.rules.yml

# RF=3 keyspace, a few writes/reads at QUORUM
test:
	docker exec scylla-1 cqlsh --file /cql/test.cql

status:
	docker exec scylla-1 nodetool status

cli:
	docker exec -it scylla-1 cqlsh

down:
	docker compose down --volumes
