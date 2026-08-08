CLUSTER_NAME := altinity-test
OPERATOR_NAMESPACE := clickhouse-operator
OPERATOR_CHART_VERSION := 0.27.2
HELM_NAMESPACE := clickhouse-helm-test
KUBECTL_NAMESPACE := clickhouse-kubectl-test

.PHONY: kind-cluster helm-implementation kubectl-implementation \
	delete-kind-cluster delete-helm-implementation delete-kubectl-implementation

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	kubectl config use-context kind-$(CLUSTER_NAME)
	helm repo add clickhouse-altinity-com https://docs.altinity.com/clickhouse-operator/ >/dev/null 2>&1 || true
	helm repo update clickhouse-altinity-com
	helm upgrade --install altinity-clickhouse-operator clickhouse-altinity-com/altinity-clickhouse-operator \
		--version $(OPERATOR_CHART_VERSION) \
		--namespace $(OPERATOR_NAMESPACE) --create-namespace \
		--set 'watchNamespaces[0]=$(HELM_NAMESPACE)' \
		--set 'watchNamespaces[1]=$(KUBECTL_NAMESPACE)'

helm-implementation: kind-cluster
	kubectl create namespace $(HELM_NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	helm upgrade --install clickhouse-test helm-implementation --namespace $(HELM_NAMESPACE)

kubectl-implementation: kind-cluster
	kubectl create namespace $(KUBECTL_NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	cd kubectl-implementation && ./render.sh
	kubectl apply -f kubectl-implementation/chk.yaml
	kubectl apply -f kubectl-implementation/chi.yaml

delete-helm-implementation:
	helm uninstall clickhouse-test --namespace $(HELM_NAMESPACE) || true
	kubectl delete namespace $(HELM_NAMESPACE) --ignore-not-found

delete-kubectl-implementation:
	kubectl delete -f kubectl-implementation/chi.yaml -f kubectl-implementation/chk.yaml --ignore-not-found
	kubectl delete namespace $(KUBECTL_NAMESPACE) --ignore-not-found

delete-kind-cluster:
	kind delete cluster --name $(CLUSTER_NAME)
