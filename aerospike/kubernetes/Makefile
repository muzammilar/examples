CLUSTER_NAME := aerospike
NAMESPACE := aerospike
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
TOOLS_IMAGE := aerospike/aerospike-tools:13.1.0

# aql exits 0 even on errors: print its output, fail on an "Error" line or when $(1) is missing
AQL := $(KUBECTL) exec -i aql-failover -- aql -h aerospike-0.aerospike -f /dev/stdin
AQL_EXPECT = 2>&1 | awk -v need='$(1)' '{print} /^Error/{e=1} index($$0, need){ok=1} END{exit e || !ok}'

.PHONY: up test failover status cli metrics down kind-cluster cluster

up: kind-cluster cluster

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE) --dry-run=client -o yaml \
		| kubectl --context kind-$(CLUSTER_NAME) apply -f -

cluster:
	$(KUBECTL) apply -f aerospike.yaml
	$(KUBECTL) rollout status statefulset/aerospike --timeout=15m
	@for i in $$(seq 60); do \
		$(KUBECTL) exec aerospike-0 -c aerospike -- asinfo -v 'cluster-stable:size=3' 2>/dev/null | grep -qv ERROR && { echo "cluster stable, size 3"; exit 0; }; \
		sleep 2; \
	done; echo "cluster not stable at size 3 after 120 s"; exit 1

# aql from a throwaway aerospike-tools pod (the first run pulls the image inside kind)
test:
	$(KUBECTL) run aql --rm -i --restart=Never --pod-running-timeout=20m --image=$(TOOLS_IMAGE) -- \
		aql -h aerospike -f /dev/stdin < aql/test.aql

# delete pod aerospike-2: the other two keep every record (RF=2); the StatefulSet recreates it
failover:
	@echo "==> starting an aerospike-tools pod to run aql from"
	$(KUBECTL) delete pod aql-failover --ignore-not-found --wait
	$(KUBECTL) run aql-failover --restart=Never --image=$(TOOLS_IMAGE) --command -- sleep 3600
	$(KUBECTL) wait --for=condition=Ready pod/aql-failover --timeout=20m
	@echo "==> emptying set test.failover, then writing 10 records (all three pods up)"
	$(KUBECTL) exec aerospike-0 -c aerospike -- asinfo -v 'truncate:namespace=test;set=failover'
	@for i in $$(seq 30); do \
		n=0; for p in aerospike-0 aerospike-1 aerospike-2; do \
			o=$$($(KUBECTL) exec $$p -c aerospike -- asinfo -v 'sets/test/failover' | tr ':;' '\n\n' | sed -n 's/^objects=//p'); \
			n=$$((n + $${o:-0})); \
		done; \
		[ $$n = 0 ] && exit 0; sleep 1; \
	done; echo "set test.failover still holds $$n records after 30 s"; exit 1
	$(AQL) < failover/write.aql $(call AQL_EXPECT,record affected)
	@echo "==> deleting pod aerospike-2"
	$(KUBECTL) delete pod aerospike-2 --wait=false
	@echo "==> waiting until the cluster re-forms at size 2 (before the StatefulSet's new aerospike-2 joins)"
	@for i in $$(seq 120); do \
		$(KUBECTL) exec aerospike-0 -c aerospike -- asinfo -v 'cluster-stable:size=2' 2>/dev/null | grep -qv ERROR && { echo "cluster stable, size 2"; exit 0; }; \
		sleep 0.5; \
	done; echo "cluster never stable at size 2 within 60 s"; exit 1
	@echo "==> reading all 10 records (the surviving replicas are masters now)"
	$(AQL) < failover/read.aql $(call AQL_EXPECT,10 rows in set)
	@echo "==> waiting for the new aerospike-2, size 3 and finished migrations"
	$(KUBECTL) rollout status statefulset/aerospike --timeout=10m
	@for i in $$(seq 120); do \
		$(KUBECTL) exec aerospike-0 -c aerospike -- asinfo -v 'cluster-stable:size=3;ignore-migrations=false' 2>/dev/null | grep -qv ERROR && { echo "cluster stable, size 3, migrations done"; exit 0; }; \
		sleep 1; \
	done; echo "cluster not stable at size 3 with migrations done after 120 s"; exit 1
	$(AQL) < failover/read.aql $(call AQL_EXPECT,10 rows in set)
	$(KUBECTL) delete pod aql-failover --wait=false

status:
	$(KUBECTL) exec aerospike-0 -c aerospike -- asadm -e 'info'

cli:
	$(KUBECTL) exec -it aerospike-0 -c aerospike -- asadm

# curl localhost:9145/metrics while this runs
metrics:
	$(KUBECTL) port-forward pod/aerospike-0 9145:9145

down:
	kind delete cluster --name $(CLUSTER_NAME)
