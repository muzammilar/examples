.PHONY: up dashboards test failover status cli down

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

# stop scylla-3: QUORUM and LWT keep working, CONSISTENCY ALL does not; then bring it back
failover:
	@echo "==> stopping scylla-3"
	docker compose stop scylla-3
	@echo "==> waiting until scylla-1 sees it down (DN)"
	for i in $$(seq 30); do docker exec scylla-1 nodetool status | grep -q '^DN' && break; sleep 2; done
	docker exec scylla-1 nodetool status | grep -E '^(UN|DN)'
	docker exec scylla-1 nodetool status | grep -q '^DN'
	@echo "==> QUORUM write + read (2 of 3 replicas)"
	docker exec -i scylla-1 cqlsh < failover/quorum.cql
	@echo "==> CONSISTENCY ALL (3 of 3 replicas)"
	docker exec -i scylla-1 cqlsh < failover/all.cql || echo "expected: Unavailable, only 2 of 3 replicas alive"
	@echo "==> lightweight transactions (Paxos quorum)"
	docker exec -i scylla-1 cqlsh < failover/lwt.cql
	@echo "==> starting scylla-3"
	docker compose start scylla-3
	@echo "==> waiting until all three nodes are UN"
	for i in $$(seq 90); do [ "$$(docker exec scylla-1 nodetool status | grep -c '^UN')" = 3 ] && break; sleep 2; done
	docker exec scylla-1 nodetool status | grep -E '^(UN|DN)'
	[ "$$(docker exec scylla-1 nodetool status | grep -c '^UN')" = 3 ]
	@echo "==> CONSISTENCY ALL works again (scylla-3 got the missed write via hints / read repair)"
	docker exec -i scylla-1 cqlsh < failover/all.cql

status:
	docker exec scylla-1 nodetool status

cli:
	docker exec -it scylla-1 cqlsh

down:
	docker compose down --volumes
