CLUSTER_NAME := aerospike
NAMESPACE := aerospike
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)

.PHONY: up test status cli metrics down kind-cluster cluster

up: kind-cluster cluster

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE) --dry-run=client -o yaml \
		| kubectl --context kind-$(CLUSTER_NAME) apply -f -

cluster:
	$(KUBECTL) apply -f aerospike.yaml
	$(KUBECTL) rollout status statefulset/aerospike --timeout=15m
	until $(KUBECTL) exec aerospike-0 -c aerospike -- asinfo -v 'cluster-stable:size=3' 2>/dev/null | grep -qv ERROR; do sleep 2; done
	@echo "cluster stable, size 3"

# aql from a throwaway aerospike-tools pod
test:
	$(KUBECTL) run aql --rm -i --restart=Never --pod-running-timeout=10m --image=aerospike/aerospike-tools:13.1.0 -- \
		aql -h aerospike -f /dev/stdin < aql/test.aql

status:
	$(KUBECTL) exec aerospike-0 -c aerospike -- asadm -e 'info'

cli:
	$(KUBECTL) exec -it aerospike-0 -c aerospike -- asadm

# curl localhost:9145/metrics while this runs
metrics:
	$(KUBECTL) port-forward pod/aerospike-0 9145:9145

down:
	kind delete cluster --name $(CLUSTER_NAME)
