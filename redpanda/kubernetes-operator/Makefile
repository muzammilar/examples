CLUSTER_NAME := redpanda-operator
NAMESPACE := redpanda
OPERATOR_VERSION := 26.2.4
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
RPK := $(KUBECTL) -n $(NAMESPACE) exec redpanda-0 -c redpanda -- rpk

.PHONY: up kind-cluster operator cluster test failover status cli down

up: kind-cluster operator cluster

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

# Redpanda Operator (redpanda/operator chart, CRDs included), cluster-scoped
operator:
	helm repo add redpanda https://charts.redpanda.com >/dev/null 2>&1 || true
	helm repo update redpanda
	$(HELM) upgrade --install redpanda-controller redpanda/operator --version $(OPERATOR_VERSION) \
		--namespace $(NAMESPACE) --create-namespace --set crds.enabled=true --wait --timeout 10m
	$(KUBECTL) -n $(NAMESPACE) rollout status deployment/redpanda-controller-operator --timeout=5m

# the Redpanda resource; Ready once the operator's Helm release is reconciled and all brokers run
cluster:
	$(KUBECTL) -n $(NAMESPACE) apply -f redpanda-cluster.yaml
	$(KUBECTL) -n $(NAMESPACE) wait redpanda/redpanda --for=condition=Ready --timeout=15m
	$(KUBECTL) -n $(NAMESPACE) wait pod/redpanda-0 pod/redpanda-1 pod/redpanda-2 --for=condition=Ready --timeout=10m
	$(KUBECTL) -n $(NAMESPACE) get redpanda,pods

test:
	$(KUBECTL) -n $(NAMESPACE) exec -i redpanda-0 -c redpanda -- bash -s < scripts/01-test.sh

# rpk producer (acks=all) in redpanda-0 while redpanda-2's pod is force-deleted; every acked
# record must be readable afterwards; the StatefulSet recreates the pod on the same PVC
failover:
	bash scripts/failover.sh

status:
	$(KUBECTL) -n $(NAMESPACE) get redpanda,pods,pvc
	$(RPK) cluster health

cli:
	$(KUBECTL) -n $(NAMESPACE) exec -it redpanda-0 -c redpanda -- bash

down:
	kind delete cluster --name $(CLUSTER_NAME)
